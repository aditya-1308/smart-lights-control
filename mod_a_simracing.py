"""
mod_a_simracing.py — Sim racing telemetry reader (rev meter fallback).

Only active when DS4 lightbar data is NOT being received (state.is_ds4_active()
returns False). Provides RPM percentage to the lightbar state machine.

Two data sources (selected by SIM_GAME in .env):

  AC  — Assetto Corsa Windows Shared Memory
        Reads Local\\acpmf_physics and Local\\acpmf_static via mmap + ctypes.
        No network, zero latency, works without any game mods.
        Note: AC + CSP will send lightbar data via the DS4 callback instead,
        making this module dormant. This path handles base AC (no CSP).

  F1  — F1 23/24 UDP telemetry
        Binds a UDP socket on F1_UDP_PORT (default 20777).
        Parses Packet ID 6 (Car Telemetry) for revLightsPercent (0-100).

Updates state.rpm_pct (0.0-1.0) at up to 30 Hz.
"""

import asyncio
import ctypes
import logging
import mmap
import socket
import struct
import time
from ctypes import Structure, c_float, c_int32, c_wchar
from typing import Optional

import config
from state import state, LightbarMode

log = logging.getLogger("simracing")

# Update cap — don't hammer state faster than necessary
_UPDATE_INTERVAL = 1.0 / 30  # 30 Hz


# ===========================================================================
# Assetto Corsa Shared Memory Structures
# ===========================================================================

class SPageFilePhysics(Structure):
    """Assetto Corsa physics shared memory page (Local\\acpmf_physics)."""
    _pack_ = 4
    _fields_ = [
        ("packetId",            c_int32),
        ("gas",                 c_float),
        ("brake",               c_float),
        ("fuel",                c_float),
        ("gear",                c_int32),
        ("rpms",                c_int32),   # Current engine RPM
        ("steerAngle",          c_float),
        ("speedKmh",            c_float),
        ("velocity",            c_float * 3),
        ("accG",                c_float * 3),
        ("wheelSlip",           c_float * 4),
        ("wheelLoad",           c_float * 4),
        ("wheelsPressure",      c_float * 4),
        ("wheelAngularSpeed",   c_float * 4),
        ("tyreWear",            c_float * 4),
        ("tyreDirtyLevel",      c_float * 4),
        ("tyreCoreTemperature", c_float * 4),
        ("camberRAD",           c_float * 4),
        ("suspensionTravel",    c_float * 4),
        ("drs",                 c_float),
        ("tc",                  c_float),
        ("heading",             c_float),
        ("pitch",               c_float),
        ("roll",                c_float),
        ("cgHeight",            c_float),
        ("carDamage",           c_float * 5),
        ("numberOfTyresOut",    c_int32),
        ("pitLimiterOn",        c_int32),
        ("abs",                 c_float),
    ]


class SPageFileStatic(Structure):
    """Assetto Corsa static shared memory page (Local\\acpmf_static)."""
    _pack_ = 4
    _fields_ = [
        ("smVersion",         c_wchar * 15),
        ("acVersion",         c_wchar * 15),
        ("numberOfSessions",  c_int32),
        ("numCars",           c_int32),
        ("carModel",          c_wchar * 33),
        ("track",             c_wchar * 33),
        ("playerName",        c_wchar * 33),
        ("playerSurname",     c_wchar * 33),
        ("playerNick",        c_wchar * 33),
        ("sectorCount",       c_int32),
        ("maxTorque",         c_float),
        ("maxPower",          c_float),
        ("maxRpm",            c_int32),    # Redline RPM
        ("maxFuel",           c_float),
    ]


class ACSharedMemoryReader:
    """Reads RPM data from Assetto Corsa's Windows shared memory."""

    _PHYSICS_NAME = "Local\\acpmf_physics"
    _STATIC_NAME = "Local\\acpmf_static"

    def __init__(self) -> None:
        self._phys_shm: Optional[mmap.mmap] = None
        self._stat_shm: Optional[mmap.mmap] = None
        self._connected = False

    def connect(self) -> bool:
        """Open shared memory. Returns True if AC is running."""
        try:
            self._phys_shm = mmap.mmap(
                -1,
                ctypes.sizeof(SPageFilePhysics),
                self._PHYSICS_NAME,
                access=mmap.ACCESS_READ,
            )
            self._stat_shm = mmap.mmap(
                -1,
                ctypes.sizeof(SPageFileStatic),
                self._STATIC_NAME,
                access=mmap.ACCESS_READ,
            )
            self._connected = True
            log.info("Assetto Corsa shared memory opened.")
            return True
        except OSError:
            # AC not running — shared memory doesn't exist yet
            return False

    def disconnect(self) -> None:
        """Close shared memory handles."""
        for shm in (self._phys_shm, self._stat_shm):
            if shm is not None:
                try:
                    shm.close()
                except Exception:
                    pass
        self._connected = False

    def read_rpm_pct(self) -> Optional[float]:
        """
        Read current RPM percentage (0.0-1.0).

        Returns None if shared memory is unavailable or maxRpm is zero.
        """
        if not self._connected:
            return None
        try:
            self._phys_shm.seek(0)
            physics = SPageFilePhysics.from_buffer_copy(self._phys_shm)
            self._stat_shm.seek(0)
            static = SPageFileStatic.from_buffer_copy(self._stat_shm)

            if static.maxRpm <= 0:
                return None

            pct = physics.rpms / static.maxRpm
            return max(0.0, min(1.0, pct))
        except Exception as exc:
            log.debug("AC shared memory read error: %s", exc)
            self._connected = False
            return None


# ===========================================================================
# F1 23 / 24 UDP Telemetry
# ===========================================================================

# F1 UDP packet header format (little-endian, 29 bytes)
_F1_HEADER_FMT = "<HBBBBBQfIIBB"
_F1_HEADER_SIZE = struct.calcsize(_F1_HEADER_FMT)

# Car telemetry data per car (partial — just the fields we need)
# speed(H) throttle(f) steer(f) brake(f) clutch(B) gear(b)
# engineRPM(H) drs(B) revLightsPercent(B)
_F1_CAR_TELEM_FMT = "<HfffBbHBB"
_F1_CAR_TELEM_SIZE = struct.calcsize(_F1_CAR_TELEM_FMT)

_F1_PACKET_ID_CAR_TELEMETRY = 6


class F1UDPReader:
    """Reads RPM data from F1 23/24 via UDP broadcast."""

    def __init__(self, port: int = 20777) -> None:
        self._port = port
        self._sock: Optional[socket.socket] = None

    def connect(self) -> bool:
        """Bind UDP socket. Always succeeds (game may not be running yet)."""
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(("0.0.0.0", self._port))
            self._sock.setblocking(False)
            log.info("F1 UDP listener bound on port %d.", self._port)
            return True
        except OSError as exc:
            log.error("Could not bind F1 UDP port %d: %s", self._port, exc)
            return False

    def disconnect(self) -> None:
        if self._sock:
            self._sock.close()
            self._sock = None

    def read_rpm_pct(self) -> Optional[float]:
        """
        Non-blocking read of the latest F1 telemetry packet.

        Returns RPM as a 0.0-1.0 percentage using the game's built-in
        revLightsPercent (0-100) field which already accounts for the
        car's specific shift point. Returns None if no packet available.
        """
        if self._sock is None:
            return None
        try:
            data, _ = self._sock.recvfrom(2048)
        except BlockingIOError:
            return None  # No data available
        except Exception:
            return None

        if len(data) < _F1_HEADER_SIZE:
            return None

        header = struct.unpack_from(_F1_HEADER_FMT, data, 0)
        packet_id = header[5]          # index 5 = packetId
        player_idx = header[10]        # index 10 = playerCarIndex

        if packet_id != _F1_PACKET_ID_CAR_TELEMETRY:
            return None

        # Each car's telemetry block follows the header
        # Full car telemetry packet size per car is ~60 bytes in F1 23/24
        car_offset = _F1_HEADER_SIZE + (player_idx * 60)
        if len(data) < car_offset + _F1_CAR_TELEM_SIZE:
            return None

        telem = struct.unpack_from(_F1_CAR_TELEM_FMT, data, car_offset)
        rev_lights_pct = telem[8]  # 0-100 directly from game
        return max(0.0, min(1.0, rev_lights_pct / 100.0))


# ===========================================================================
# Module entry point
# ===========================================================================

async def run() -> None:
    """
    Async task: continuously update state.rpm_pct from game telemetry.

    Automatically reconnects if the game closes and reopens.
    Dormant (returns immediately) if DS4 lightbar data is active.
    """
    game = config.SIM_GAME.upper()
    log.info("Sim racing module starting (SIM_GAME=%s).", game)

    if game == "AC":
        await _run_ac()
    elif game == "F1":
        await _run_f1()
    else:
        log.error("Unknown SIM_GAME '%s'. Set to 'AC' or 'F1' in .env.", game)


async def _run_ac() -> None:
    """Assetto Corsa shared memory polling loop."""
    reader = ACSharedMemoryReader()
    last_update = 0.0
    connected = False

    while not state.shutdown_event.is_set():
        # Try to connect if not already
        if not connected:
            connected = await asyncio.to_thread(reader.connect)
            if not connected:
                # AC not running yet — check again in 3s
                await asyncio.sleep(3.0)
                continue

        # Skip if DS4 lightbar is active (DS4 has priority)
        if state.is_ds4_active(config.DS4_LIGHTBAR_TIMEOUT):
            await asyncio.sleep(0.5)
            continue

        now = time.monotonic()
        if now - last_update < _UPDATE_INTERVAL:
            await asyncio.sleep(_UPDATE_INTERVAL - (now - last_update))
            continue

        pct = await asyncio.to_thread(reader.read_rpm_pct)
        if pct is None:
            # Shared memory gone — AC probably closed
            reader.disconnect()
            connected = False
            state.rpm_pct = 0.0
            await asyncio.sleep(3.0)
            continue

        state.rpm_pct = pct
        last_update = time.monotonic()

    reader.disconnect()
    log.info("AC reader stopped.")


async def _run_f1() -> None:
    """F1 23/24 UDP polling loop."""
    reader = F1UDPReader(config.F1_UDP_PORT)
    if not await asyncio.to_thread(reader.connect):
        log.error("F1 UDP reader failed to start.")
        return

    last_update = 0.0

    while not state.shutdown_event.is_set():
        # Skip if DS4 lightbar is active
        if state.is_ds4_active(config.DS4_LIGHTBAR_TIMEOUT):
            await asyncio.sleep(0.5)
            continue

        now = time.monotonic()
        if now - last_update < _UPDATE_INTERVAL:
            await asyncio.sleep(max(0, _UPDATE_INTERVAL - (now - last_update)))
            continue

        pct = await asyncio.to_thread(reader.read_rpm_pct)
        if pct is not None:
            state.rpm_pct = pct
            last_update = time.monotonic()
        else:
            await asyncio.sleep(0.01)  # No packet yet — tight loop

    reader.disconnect()
    log.info("F1 UDP reader stopped.")
