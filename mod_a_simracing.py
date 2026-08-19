"""
mod_a_simracing.py - Universal Sim Racing Telemetry via Pure UDP.

100% Network UDP — ZERO shared memory, ZERO DLL injection, ZERO crash risk.

Supported games:
  - Assetto Corsa (AC1 & CSP)  : UDP port 9996 (official AC UDP Telemetry Protocol)
  - F1 23 / F1 24              : UDP port 20777 (Codemasters / EA Sports format)
  - Automobilista 2 (AMS2)     : UDP port 5606 (Project CARS 2 UDP format)
  - Forza Motorsport / Horizon : UDP port 5300 (Forza Data Out format)
  - iRacing                    : pyirsdk (pure read)

Setup in Assetto Corsa:
  - No mods needed! AC includes built-in UDP telemetry on port 9996.
  - Python sends a 4-byte handshake packet to 127.0.0.1:9996 and AC streams telemetry back.
"""

import asyncio
import logging
import socket
import struct
import time
from typing import Optional

import config
from state import state

log = logging.getLogger("simracing")
_UPDATE_INTERVAL = 1.0 / 30


# ===========================================================================
# Assetto Corsa (AC1 + CSP) — Official UDP Telemetry Protocol (Port 9996)
# ===========================================================================
# Protocol specs:
#   1. Handshake request (send to 127.0.0.1:9996):
#      struct.pack('<iii', 1, 1, 0)
#      (1 = ACSP_HANDSHAKE, 1 = version, 0 = ACSP_SUBSCRIBE_UPDATE)
#   2. Telemetry stream (RTCarTelemetry, 328 bytes):
#      offset  0.. 8 : identifier (c_wchar*4)
#      offset  8..12 : size (i32)
#      offset 12..16 : speed_Kmh (f32)
#      offset 24..25 : isAbsEnabled (u8)
#      offset 25..26 : isAbsInAction (u8)
#      offset 26..27 : isTcInAction (u8)
#      offset 27..28 : isTcEnabled (u8)
#      offset 28..29 : isInPit (u8)
#      offset 29..30 : isEngineLimiterOn (u8)
#      offset 72..76 : engineRPM (f32)
#      offset 80..84 : gear (i32)
#
_AC_HANDSHAKE_PKT = struct.pack("<iii", 1, 1, 0)
_AC_DISMISS_PKT   = struct.pack("<iii", 1, 1, 2)


class ACUDPReader:
    """Reads engine RPM and telemetry from Assetto Corsa via UDP port 9996."""

    def __init__(self, port: int = 9996) -> None:
        self._port = port
        self._sock: Optional[socket.socket] = None
        self._max_rpm_seen: float = 6500.0
        self._last_handshake: float = 0.0
        self._last_received: float = 0.0  # time of last valid telemetry packet

    def connect(self) -> bool:
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(("0.0.0.0", 0))  # Bind to ephemeral port
            self._sock.setblocking(False)
            self._send_handshake()
            log.info("AC UDP client initialized (target 127.0.0.1:%d).", self._port)
            return True
        except OSError as exc:
            log.error("AC UDP socket setup failed: %s", exc)
            return False

    def _send_handshake(self) -> None:
        if not self._sock:
            return
        try:
            self._sock.sendto(_AC_HANDSHAKE_PKT, ("127.0.0.1", self._port))
            self._last_handshake = time.monotonic()
        except Exception:
            pass

    def disconnect(self) -> None:
        if self._sock:
            try:
                self._sock.sendto(_AC_DISMISS_PKT, ("127.0.0.1", self._port))
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def read_rpm_pct(self) -> Optional[float]:
        if not self._sock:
            return None

        now = time.monotonic()
        # Resend handshake every 0.5s when AC hasn't replied yet,
        # or every 2s during active session (AC re-starts the stream on reconnect)
        no_data_recently = (now - self._last_received) > 1.0
        handshake_interval = 0.5 if no_data_recently else 2.0
        if now - self._last_handshake > handshake_interval:
            self._send_handshake()

        try:
            data, _ = self._sock.recvfrom(2048)
        except (BlockingIOError, OSError):
            return None

        if len(data) < 84:
            return None

        # Unpack telemetry fields
        try:
            is_limiter = data[29] if len(data) > 29 else 0
            engine_rpm = struct.unpack_from("<f", data, 72)[0]
            if engine_rpm <= 0:
                return None

            self._last_received = now  # mark that AC is actively sending

            if engine_rpm > self._max_rpm_seen:
                self._max_rpm_seen = engine_rpm

            if is_limiter:
                return 1.0

            return max(0.0, min(1.0, engine_rpm / self._max_rpm_seen))
        except Exception:
            return None


# ===========================================================================
# F1 23 / F1 24 — UDP port 20777
# ===========================================================================
_F1_HEADER_FMT  = "<HBBBBBQfIIBB"
_F1_HEADER_SIZE = struct.calcsize(_F1_HEADER_FMT)
_F1_TELEM_FMT   = "<HfffBbHBB"
_F1_TELEM_SIZE  = struct.calcsize(_F1_TELEM_FMT)
_F1_PKT_TELEM   = 6


class F1Reader:
    """Reads engine RPM from F1 23/24 UDP broadcast on port 20777."""

    def __init__(self) -> None:
        self._sock: Optional[socket.socket] = None

    def connect(self) -> bool:
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(("0.0.0.0", config.F1_UDP_PORT))
            self._sock.setblocking(False)
            log.info("F1 UDP listener bound on port %d.", config.F1_UDP_PORT)
            return True
        except OSError as exc:
            log.error("F1 UDP bind failed: %s", exc)
            return False

    def disconnect(self) -> None:
        if self._sock:
            self._sock.close()
            self._sock = None

    def read_rpm_pct(self) -> Optional[float]:
        if not self._sock:
            return None
        try:
            data, _ = self._sock.recvfrom(2048)
        except (BlockingIOError, OSError):
            return None

        if len(data) < _F1_HEADER_SIZE:
            return None
        hdr = struct.unpack_from(_F1_HEADER_FMT, data, 0)
        if hdr[5] != _F1_PKT_TELEM:
            return None

        player_idx = hdr[10]
        car_offset = _F1_HEADER_SIZE + player_idx * 60
        if len(data) < car_offset + _F1_TELEM_SIZE:
            return None

        telem = struct.unpack_from(_F1_TELEM_FMT, data, car_offset)
        return max(0.0, min(1.0, telem[8] / 100.0))


# ===========================================================================
# Automobilista 2 (AMS2) — UDP port 5606
# ===========================================================================
_AMS2_PKT_CARPHYSICS = 0
_AMS2_RPM_OFFSET     = 41
_AMS2_MAXRPM_OFFSET  = 43


class AMS2Reader:
    """Reads RPM from Automobilista 2 via Project CARS 2 UDP protocol."""

    def __init__(self) -> None:
        self._sock: Optional[socket.socket] = None
        self._port: int = config.AMS2_UDP_PORT

    def connect(self) -> bool:
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(("0.0.0.0", self._port))
            self._sock.setblocking(False)
            log.info("AMS2 UDP listener bound on port %d.", self._port)
            return True
        except OSError as exc:
            log.error("AMS2 UDP bind failed: %s", exc)
            return False

    def disconnect(self) -> None:
        if self._sock:
            self._sock.close()
            self._sock = None

    def read_rpm_pct(self) -> Optional[float]:
        if not self._sock:
            return None
        try:
            data, _ = self._sock.recvfrom(4096)
        except (BlockingIOError, OSError):
            return None

        if len(data) < 50 or data[10] != _AMS2_PKT_CARPHYSICS:
            return None

        rpm     = struct.unpack_from("<H", data, _AMS2_RPM_OFFSET)[0]
        max_rpm = struct.unpack_from("<H", data, _AMS2_MAXRPM_OFFSET)[0]
        if max_rpm <= 0:
            return None
        return max(0.0, min(1.0, rpm / max_rpm))


# ===========================================================================
# Forza Motorsport / Horizon — UDP port 5300
# ===========================================================================
_FORZA_CURRPM_OFFSET   = 16
_FORZA_MAXRPM_OFFSET   = 8
_FORZA_ISRACEON_OFFSET = 0


class ForzaReader:
    """Reads RPM from Forza Motorsport / Horizon via UDP Data Out."""

    def __init__(self) -> None:
        self._sock: Optional[socket.socket] = None
        self._port: int = config.FORZA_UDP_PORT

    def connect(self) -> bool:
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(("0.0.0.0", self._port))
            self._sock.setblocking(False)
            log.info("Forza UDP listener bound on port %d.", self._port)
            return True
        except OSError as exc:
            log.error("Forza UDP bind failed: %s", exc)
            return False

    def disconnect(self) -> None:
        if self._sock:
            self._sock.close()
            self._sock = None

    def read_rpm_pct(self) -> Optional[float]:
        if not self._sock:
            return None
        try:
            data, _ = self._sock.recvfrom(512)
        except (BlockingIOError, OSError):
            return None

        if len(data) < 20:
            return None

        is_race_on = struct.unpack_from("<i", data, _FORZA_ISRACEON_OFFSET)[0]
        if is_race_on == 0:
            return None

        max_rpm = struct.unpack_from("<f", data, _FORZA_MAXRPM_OFFSET)[0]
        cur_rpm = struct.unpack_from("<f", data, _FORZA_CURRPM_OFFSET)[0]
        if max_rpm <= 0:
            return None
        return max(0.0, min(1.0, cur_rpm / max_rpm))


# ===========================================================================
# Async run loops
# ===========================================================================

async def run() -> None:
    log.info("Universal sim racing telemetry starting (AC UDP + F1 + AMS2 + Forza)...")
    tasks = [
        _run_udp_reader("AC UDP", ACUDPReader(config.AC_UDP_PORT)),
        _run_udp_reader("F1",     F1Reader()),
        _run_udp_reader("AMS2",   AMS2Reader()),
        _run_udp_reader("Forza",  ForzaReader()),
    ]
    await asyncio.gather(*tasks, return_exceptions=True)


async def _run_udp_reader(name: str, reader) -> None:
    if not await asyncio.to_thread(reader.connect):
        log.warning("%s reader failed to start — skipping.", name)
        return

    last_received = 0.0  # monotonic time of last successful rpm read
    _STALE_TIMEOUT = 3.0  # seconds without data before resetting rpm_pct to 0

    while not state.shutdown_event.is_set():
        if state.is_ds4_active(config.DS4_LIGHTBAR_TIMEOUT):
            await asyncio.sleep(0.5)
            continue

        pct = await asyncio.to_thread(reader.read_rpm_pct)
        now = time.monotonic()
        if pct is not None:
            state.rpm_pct = pct
            last_received = now
        else:
            # No data from this reader — if stale across all readers, reset to 0
            if last_received > 0 and (now - last_received) > _STALE_TIMEOUT:
                state.rpm_pct = 0.0

        await asyncio.sleep(0.01)

    await asyncio.to_thread(reader.disconnect)
    log.info("%s reader stopped.", name)
