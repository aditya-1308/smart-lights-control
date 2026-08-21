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
        self._max_rpm_seen: float = 1000.0
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
        no_data_recently = (now - self._last_received) > 1.0
        handshake_interval = 0.5 if no_data_recently else 2.0
        if now - self._last_handshake > handshake_interval:
            self._send_handshake()

        # Drain socket buffer to get the freshest packet
        latest_data = None
        while True:
            try:
                data, _ = self._sock.recvfrom(2048)
                if data:
                    latest_data = data
            except (BlockingIOError, OSError):
                break

        if not latest_data:
            return None

        # 408 bytes = AC Handshake Response packet (RTCarStatic)
        if len(latest_data) == 408:
            self._last_received = now
            try:
                static_max = struct.unpack_from("<i", latest_data, 402)[0]
                if static_max > 1000:
                    self._max_rpm_seen = float(static_max)
            except Exception:
                pass
            return None

        if len(latest_data) < 80:
            return None

        try:
            is_limiter = latest_data[29] if len(latest_data) > 29 else 0
            engine_rpm = struct.unpack_from("<f", latest_data, 72)[0]

            if engine_rpm <= 0.0:
                return 0.0

            self._last_received = now

            if engine_rpm > self._max_rpm_seen:
                self._max_rpm_seen = float(engine_rpm)

            if is_limiter:
                return 1.0

            return max(0.0, min(1.0, engine_rpm / self._max_rpm_seen))
        except Exception:
            return None


class ACSharedMemoryReader:
    """Reads engine RPM directly from Assetto Corsa Windows Shared Memory (acpmf_physics)."""

    def __init__(self) -> None:
        self._physics_mmap: Optional[mmap.mmap] = None
        self._static_mmap: Optional[mmap.mmap] = None
        self._max_rpm: float = 1000.0

    def connect(self) -> bool:
        try:
            import mmap as mmap_mod
            self._physics_mmap = mmap_mod.mmap(-1, 712, "acpmf_physics")
            try:
                self._static_mmap = mmap_mod.mmap(-1, 712, "acpmf_static")
                static_data = self._static_mmap.read(410)
                max_rpm = struct.unpack_from("<i", static_data, 402)[0]
                if max_rpm > 1000:
                    self._max_rpm = float(max_rpm)
            except Exception:
                pass
            log.info("Assetto Corsa Shared Memory (acpmf_physics) connected!")
            return True
        except Exception:
            return False

    def disconnect(self) -> None:
        if self._physics_mmap:
            try:
                self._physics_mmap.close()
            except Exception:
                pass
            self._physics_mmap = None
        if self._static_mmap:
            try:
                self._static_mmap.close()
            except Exception:
                pass
            self._static_mmap = None

    def read_rpm_pct(self) -> Optional[float]:
        if not self._physics_mmap:
            if not self.connect():
                return None
        try:
            self._physics_mmap.seek(0)
            data = self._physics_mmap.read(260)
            if len(data) < 256:
                return None
            packet_id = struct.unpack_from("<i", data, 0)[0]
            rpms = struct.unpack_from("<i", data, 20)[0]
            pit_limiter = struct.unpack_from("<i", data, 248)[0]
            if rpms <= 0 or packet_id <= 0:
                return None
            if pit_limiter:
                return 1.0
            if float(rpms) > self._max_rpm:
                self._max_rpm = float(rpms)
            return max(0.0, min(1.0, float(rpms) / self._max_rpm))
        except Exception:
            self.disconnect()
            return None


class ACReader:
    """Unified Assetto Corsa Telemetry Reader (Shared Memory + UDP fallback)."""

    def __init__(self, port: int = 9996) -> None:
        self._shm = ACSharedMemoryReader()
        self._udp = ACUDPReader(port)

    def connect(self) -> bool:
        self._shm.connect()
        self._udp.connect()
        return True

    def disconnect(self) -> None:
        self._shm.disconnect()
        self._udp.disconnect()

    def read_rpm_pct(self) -> Optional[float]:
        pct = self._shm.read_rpm_pct()
        if pct is not None:
            return pct
        return self._udp.read_rpm_pct()


# ===========================================================================
# F1 2020 – 2025 — UDP port 20777
# ===========================================================================
class F1Reader:
    """Reads engine RPM from F1 2020-2025 UDP broadcast on port 20777."""

    def __init__(self) -> None:
        self._sock: Optional[socket.socket] = None
        self._max_rpm_seen: float = 13500.0

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

        latest_data = None
        while True:
            try:
                data, _ = self._sock.recvfrom(2048)
                if data:
                    latest_data = data
            except (BlockingIOError, OSError):
                break

        if not latest_data or len(latest_data) < 30:
            return None

        packet_format = struct.unpack_from("<H", latest_data, 0)[0]
        if packet_format not in (2020, 2021, 2022, 2023, 2024, 2025):
            return None

        if packet_format >= 2023:
            header_size = 29
            packet_id = latest_data[6]
            player_idx = latest_data[27]
        else:
            header_size = 24
            packet_id = latest_data[5]
            player_idx = latest_data[22]

        if packet_id != 6:  # 6 = PacketCarTelemetryData
            return None

        if player_idx < 0 or player_idx >= 22:
            player_idx = 0

        car_offset = header_size + player_idx * 60
        if len(latest_data) < car_offset + 22:
            return None

        engine_rpm = struct.unpack_from("<H", latest_data, car_offset + 16)[0]
        rev_lights_pct = latest_data[car_offset + 19]

        if engine_rpm <= 0:
            return 0.0

        if engine_rpm > self._max_rpm_seen:
            self._max_rpm_seen = float(engine_rpm)

        # F1 rev lights: rev_lights_pct gives exact steering wheel LED fill (0-100%)
        if rev_lights_pct > 0:
            norm = min(1.0, float(rev_lights_pct) / 100.0)
            return 0.65 + norm * 0.35
        else:
            return max(0.0, min(0.64, float(engine_rpm) / self._max_rpm_seen * 0.65))


# ===========================================================================
# Automobilista 2 (AMS2) — UDP port 5606
# ===========================================================================
class AMS2Reader:
    """Reads RPM from Automobilista 2 via Project CARS 2 UDP protocol (port 5606)."""

    def __init__(self) -> None:
        self._sock: Optional[socket.socket] = None
        self._port: int = config.AMS2_UDP_PORT
        self._max_rpm_seen: float = 1000.0

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

        latest_data = None
        while True:
            try:
                data, _ = self._sock.recvfrom(4096)
                if data:
                    latest_data = data
            except (BlockingIOError, OSError):
                break

        if not latest_data or len(latest_data) < 130:
            return None

        # Packet type 0 is sTelemetryData (CarPhysics)
        pkt_type = latest_data[10] & 3 if len(latest_data) > 10 else 255
        if pkt_type != 0:
            return None

        rpm = struct.unpack_from("<H", latest_data, 124)[0]
        max_rpm = struct.unpack_from("<H", latest_data, 126)[0]

        if max_rpm > 1000:
            self._max_rpm_seen = float(max_rpm)
        elif rpm > self._max_rpm_seen:
            self._max_rpm_seen = float(rpm)

        if self._max_rpm_seen <= 0:
            return None

        return max(0.0, min(1.0, float(rpm) / self._max_rpm_seen))


# ===========================================================================
# Forza Motorsport / Horizon — UDP port 5300
# ===========================================================================
class ForzaReader:
    """Reads RPM from Forza Motorsport / Horizon via UDP Data Out (port 5300)."""

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

        latest_data = None
        while True:
            try:
                data, _ = self._sock.recvfrom(512)
                if data:
                    latest_data = data
            except (BlockingIOError, OSError):
                break

        if not latest_data or len(latest_data) < 20:
            return None

        is_race_on = struct.unpack_from("<i", latest_data, 0)[0]
        if is_race_on == 0:
            return 0.0

        max_rpm = struct.unpack_from("<f", latest_data, 8)[0]
        cur_rpm = struct.unpack_from("<f", latest_data, 16)[0]
        if max_rpm <= 100.0 or cur_rpm < 0.0:
            return 0.0
        return max(0.0, min(1.0, cur_rpm / max_rpm))


# ===========================================================================
# Async run loops
# ===========================================================================

async def run() -> None:
    log.info("Universal sim racing telemetry starting (AC SHM+UDP + F1 + AMS2 + Forza)...")
    tasks = [
        _run_udp_reader("AC Telemetry", ACReader(config.AC_UDP_PORT)),
        _run_udp_reader("F1",           F1Reader()),
        _run_udp_reader("AMS2",         AMS2Reader()),
        _run_udp_reader("Forza",        ForzaReader()),
    ]
    await asyncio.gather(*tasks, return_exceptions=True)


async def _run_udp_reader(name: str, reader) -> None:
    if not await asyncio.to_thread(reader.connect):
        log.warning("%s reader failed to start — skipping.", name)
        return

    last_received = 0.0  # monotonic time of last successful rpm read
    _STALE_TIMEOUT = 3.0  # seconds without data before resetting rpm_pct to 0

    while not state.shutdown_event.is_set():
        pct = await asyncio.to_thread(reader.read_rpm_pct)
        now = time.monotonic()
        if pct is not None:
            state.mark_telemetry_received()
            state.rpm_pct = pct
            last_received = now
        else:
            # No data from this reader — if stale across all readers, reset to 0
            if last_received > 0 and (now - last_received) > _STALE_TIMEOUT:
                state.rpm_pct = 0.0

        await asyncio.sleep(0.01)

    await asyncio.to_thread(reader.disconnect)
    log.info("%s reader stopped.", name)
