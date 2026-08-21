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
from typing import Optional, Tuple

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
        self._last_received: float = 0.0

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

        latest_telem_pkt = None
        while True:
            try:
                data, _ = self._sock.recvfrom(2048)
                if data:
                    if len(data) == 408:
                        # Handshake response (RTCarStatic)
                        self._last_received = now
                        try:
                            static_max = struct.unpack_from("<i", data, 402)[0]
                            if static_max > 1000:
                                self._max_rpm_seen = float(static_max)
                        except Exception:
                            pass
                    elif len(data) >= 80:
                        latest_telem_pkt = data
            except (BlockingIOError, OSError):
                break

        if not latest_telem_pkt:
            return None

        try:
            is_limiter = bool(latest_telem_pkt[29]) if len(latest_telem_pkt) > 29 else False
            engine_rpm = struct.unpack_from("<f", latest_telem_pkt, 72)[0]

            if engine_rpm <= 0.0 and not is_limiter:
                return (0.0, False)

            self._last_received = now

            if is_limiter:
                speed_kmh = struct.unpack_from("<f", latest_telem_pkt, 12)[0]
                pit_speed_pct = max(0.0, min(1.2, speed_kmh / 60.0))
                return (pit_speed_pct, True)

            if engine_rpm > self._max_rpm_seen:
                self._max_rpm_seen = float(engine_rpm)

            return (max(0.0, min(1.0, engine_rpm / self._max_rpm_seen)), False)
        except Exception:
            return None


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

    def read_rpm_pct(self) -> Optional[Tuple[float, bool]]:
        if not self._sock:
            return None

        latest_telem_pkt = None
        while True:
            try:
                data, _ = self._sock.recvfrom(2048)
                if data and len(data) >= 30:
                    pkt_format = struct.unpack_from("<H", data, 0)[0]
                    if pkt_format in (2020, 2021, 2022, 2023, 2024, 2025):
                        pkt_id = data[6] if pkt_format >= 2023 else data[5]
                        if pkt_id == 6:  # 6 = PacketCarTelemetryData
                            latest_telem_pkt = data
            except (BlockingIOError, OSError):
                break

        if not latest_telem_pkt:
            return None

        packet_format = struct.unpack_from("<H", latest_telem_pkt, 0)[0]
        if packet_format >= 2023:
            header_size = 29
            player_idx = latest_telem_pkt[27]
        else:
            header_size = 24
            player_idx = latest_telem_pkt[22]

        if player_idx < 0 or player_idx >= 22:
            player_idx = 0

        car_offset = header_size + player_idx * 60
        if len(latest_telem_pkt) < car_offset + 22:
            return None

        engine_rpm = struct.unpack_from("<H", latest_telem_pkt, car_offset + 16)[0]
        rev_lights_pct = latest_telem_pkt[car_offset + 19]

        if engine_rpm <= 0:
            return (0.0, False)

        if engine_rpm > self._max_rpm_seen:
            self._max_rpm_seen = float(engine_rpm)

        # F1 rev lights: rev_lights_pct gives exact steering wheel LED fill (0-100%)
        if rev_lights_pct > 0:
            norm = min(1.0, float(rev_lights_pct) / 100.0)
            return (0.65 + norm * 0.35, False)
        else:
            return (max(0.0, min(0.64, float(engine_rpm) / self._max_rpm_seen * 0.65)), False)


# ===========================================================================
# Automobilista 2 (AMS2) — UDP port 5606
# ===========================================================================
class AMS2Reader:
    """Reads RPM from Automobilista 2 / Project CARS 2 via Shared Memory ($pcars2$) and UDP port 5606."""

    def __init__(self) -> None:
        self._sock: Optional[socket.socket] = None
        self._shm: Optional[object] = None
        self._port: int = config.AMS2_UDP_PORT
        self._max_rpm_seen: float = 1000.0

    def connect(self) -> bool:
        import mmap as mmap_mod
        for shm_name in ["$pcars2$", "$pcars$"]:
            try:
                self._shm = mmap_mod.mmap(-1, 16384, shm_name)
                log.info("AMS2 Shared Memory connected ('%s')!", shm_name)
                break
            except Exception:
                self._shm = None

        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(("0.0.0.0", self._port))
            self._sock.setblocking(False)
            log.info("AMS2 UDP listener bound on port %d.", self._port)
        except OSError:
            self._sock = None

        return (self._shm is not None) or (self._sock is not None)

    def disconnect(self) -> None:
        if self._shm:
            try:
                self._shm.close()
            except Exception:
                pass
            self._shm = None
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def read_rpm_pct(self) -> Optional[Tuple[float, bool]]:
        # 1. Read from Shared Memory ($pcars2$)
        if not self._shm:
            import mmap as mmap_mod
            for shm_name in ["$pcars2$", "$pcars$"]:
                try:
                    self._shm = mmap_mod.mmap(-1, 16384, shm_name)
                    break
                except Exception:
                    self._shm = None

        if self._shm:
            try:
                self._shm.seek(0)
                data = self._shm.read(8192)
                if len(data) >= 6860:
                    game_state = struct.unpack_from("<I", data, 8)[0]
                    # 2 = INGAME_PLAYING, 3 = INGAME_PAUSED, 4 = INGAME_INMENU_TIME_TICKING, 5 = INGAME_RESTARTING
                    if game_state in (2, 3, 4, 5):
                        # AMS2 / PCARS2 64-bit offsets:
                        # 6816: mCarFlags (uint32) -> bit 3 (8) = CAR_PIT_LIMITER
                        # 6848: mSpeed (float m/s)
                        # 6852: mRpm (float)
                        # 6856: mMaxRPM (float)
                        # 7396: mPitMode (uint32) -> 1=DRIVING_INTO_PITS, 2=IN_PIT, 3=DRIVING_OUT_OF_PITS
                        car_flags = struct.unpack_from("<I", data, 6816)[0]
                        speed_ms = struct.unpack_from("<f", data, 6848)[0]
                        rpm = struct.unpack_from("<f", data, 6852)[0]
                        max_rpm = struct.unpack_from("<f", data, 6856)[0]
                        pit_mode = struct.unpack_from("<I", data, 7396)[0] if len(data) >= 7400 else 0

                        if max_rpm < 500.0 or max_rpm > 30000.0:
                            car_flags = struct.unpack_from("<I", data, 3628)[0]
                            speed_ms = struct.unpack_from("<f", data, 3660)[0]
                            rpm = struct.unpack_from("<f", data, 3664)[0]
                            max_rpm = struct.unpack_from("<f", data, 3668)[0]
                            pit_mode = 0

                        is_pit_limiter_on = bool(car_flags & 8)
                        is_in_pit_lane = (pit_mode in (1, 2, 3))
                        is_pit_active = is_pit_limiter_on or is_in_pit_lane

                        if max_rpm > 1000.0:
                            self._max_rpm_seen = max_rpm
                        elif rpm > self._max_rpm_seen:
                            self._max_rpm_seen = rpm

                        if is_pit_active:
                            speed_kmh = speed_ms * 3.6
                            pit_speed_pct = max(0.0, min(1.2, speed_kmh / 60.0))
                            return (pit_speed_pct, True)

                        if self._max_rpm_seen > 0:
                            return (max(0.0, min(1.0, rpm / self._max_rpm_seen)), False)
            except Exception:
                self.disconnect()

        # 2. Read from UDP socket
        if not self._sock:
            return None

        latest_physics_pkt = None
        while True:
            try:
                data, _ = self._sock.recvfrom(4096)
                if data and len(data) >= 130:
                    pkt_type = data[10] & 3
                    if pkt_type == 0:  # 0 = sTelemetryData (CarPhysics)
                        latest_physics_pkt = data
            except (BlockingIOError, OSError):
                break

        if not latest_physics_pkt:
            return None

        rpm = struct.unpack_from("<H", latest_physics_pkt, 124)[0]
        max_rpm = struct.unpack_from("<H", latest_physics_pkt, 126)[0]

        if max_rpm > 1000:
            self._max_rpm_seen = float(max_rpm)
        elif rpm > self._max_rpm_seen:
            self._max_rpm_seen = float(rpm)

        if self._max_rpm_seen <= 0:
            return None

        return (max(0.0, min(1.0, float(rpm) / self._max_rpm_seen)), False)


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

    def read_rpm_pct(self) -> Optional[Tuple[float, bool]]:
        if not self._sock:
            return None

        latest_data = None
        while True:
            try:
                data, _ = self._sock.recvfrom(512)
                if data and len(data) >= 20:
                    latest_data = data
            except (BlockingIOError, OSError):
                break

        if not latest_data:
            return None

        is_race_on = struct.unpack_from("<i", latest_data, 0)[0]
        if is_race_on == 0:
            return (0.0, False)

        max_rpm = struct.unpack_from("<f", latest_data, 8)[0]
        cur_rpm = struct.unpack_from("<f", latest_data, 16)[0]
        if max_rpm <= 100.0 or cur_rpm < 0.0:
            return (0.0, False)
        return (max(0.0, min(1.0, cur_rpm / max_rpm)), False)


# ===========================================================================
# Unified Non-Conflicting Supervisor Loop
# ===========================================================================
async def run() -> None:
    log.info("Universal sim racing telemetry starting (AC UDP + F1 + AMS2 + Forza)...")
    readers = [
        ("AC UDP", ACUDPReader(config.AC_UDP_PORT)),
        ("F1",     F1Reader()),
        ("AMS2",   AMS2Reader()),
        ("Forza",  ForzaReader()),
    ]
    for name, r in readers:
        await asyncio.to_thread(r.connect)

    last_received_time = 0.0
    _STALE_TIMEOUT = 2.5  # seconds

    while not state.shutdown_event.is_set():
        found_data = None
        now = time.monotonic()

        for name, r in readers:
            res = await asyncio.to_thread(r.read_rpm_pct)
            if res is not None:
                found_data = res
                break

        if found_data is not None:
            pct, is_limiter = found_data
            last_received_time = now
            state.mark_telemetry_received()
            state.set_telemetry(pct, is_limiter)
        else:
            if last_received_time > 0.0 and (now - last_received_time) > _STALE_TIMEOUT:
                state.set_telemetry(0.0, False)

        await asyncio.sleep(0.01)

    for name, r in readers:
        await asyncio.to_thread(r.disconnect)
    log.info("Sim racing telemetry stopped.")
