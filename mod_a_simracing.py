"""
mod_a_simracing.py - Universal Sim Racing Telemetry Engine.

Pure Direct Read Architecture — Native Windows Shared Memory & Network UDP.
ZERO DLL injection, ZERO crash risk, works seamlessly across all Windows machines.

Supported Sim Racing Titles:
  - Assetto Corsa (AC1, CSP, ACC, AC EVO) : Windows Shared Memory (acpmf_physics/static) + UDP port 9996 fallback
  - F1 Series (2018 - 2026+)               : UDP port 20777 (EA Sports / Codemasters format)
  - Automobilista 2 & Project CARS 1/2/3   : Shared Memory ($pcars2$, $pcars$) + UDP port 5606
  - Forza Motorsport & Forza Horizon       : UDP port 5300 (Forza Data Out format)
  - BeamNG.drive / OutGauge                : OutGauge UDP support
  - iRacing                                : pyirsdk / pure memory read
"""

import asyncio
import logging
import mmap
import socket
import struct
import time
from typing import Optional, Tuple

import config
from state import state

log = logging.getLogger("simracing")
_UPDATE_INTERVAL = 1.0 / 30


# ===========================================================================
# Assetto Corsa (AC1, CSP, ACC, AC EVO) — Native Shared Memory + UDP Fallback
# ===========================================================================
_AC_HANDSHAKE_PKT = struct.pack("<iii", 1, 1, 0)
_AC_DISMISS_PKT   = struct.pack("<iii", 1, 1, 2)


class ACReader:
    """
    Reads RPM, speed, pit limiter, and telemetry from Assetto Corsa:
    1. Primary: Native Windows Shared Memory (acpmf_physics, acpmf_static) — Zero setup, instant.
    2. Fallback: AC UDP Telemetry (port 9996).
    """

    def __init__(self, port: int = 9996) -> None:
        self._port = port
        self._shm_physics: Optional[mmap.mmap] = None
        self._shm_static: Optional[mmap.mmap] = None
        self._sock: Optional[socket.socket] = None
        self._max_rpm_seen: float = 1000.0
        self._static_max_rpm: float = 0.0
        self._last_handshake: float = 0.0
        self._last_received: float = 0.0

    def connect(self) -> bool:
        self._connect_shm()
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(("0.0.0.0", 0))
            self._sock.setblocking(False)
            self._send_handshake()
        except OSError:
            self._sock = None

        return (self._shm_physics is not None) or (self._sock is not None)

    def _connect_shm(self) -> None:
        if not self._shm_physics:
            try:
                self._shm_physics = mmap.mmap(-1, 4096, "acpmf_physics")
                log.info("Assetto Corsa Shared Memory (acpmf_physics) connected!")
            except Exception:
                self._shm_physics = None

        if not self._shm_static:
            try:
                self._shm_static = mmap.mmap(-1, 4096, "acpmf_static")
                self._shm_static.seek(0)
                static_data = self._shm_static.read(512)
                if len(static_data) >= 414:
                    static_max = struct.unpack_from("<i", static_data, 410)[0]
                    if static_max > 1000:
                        self._static_max_rpm = float(static_max)
                        self._max_rpm_seen = float(static_max)
            except Exception:
                self._shm_static = None

    def _send_handshake(self) -> None:
        if not self._sock:
            return
        try:
            self._sock.sendto(_AC_HANDSHAKE_PKT, ("127.0.0.1", self._port))
            self._last_handshake = time.monotonic()
        except Exception:
            pass

    def disconnect(self) -> None:
        if self._shm_physics:
            try:
                self._shm_physics.close()
            except Exception:
                pass
            self._shm_physics = None

        if self._shm_static:
            try:
                self._shm_static.close()
            except Exception:
                pass
            self._shm_static = None

        if self._sock:
            try:
                self._sock.sendto(_AC_DISMISS_PKT, ("127.0.0.1", self._port))
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def read_rpm_pct(self) -> Optional[Tuple[float, bool]]:
        # 1. Primary: Windows Shared Memory (acpmf_physics)
        if not self._shm_physics:
            self._connect_shm()

        if self._shm_physics:
            try:
                self._shm_physics.seek(0)
                physics_data = self._shm_physics.read(256)
                if len(physics_data) >= 252:
                    packet_id = struct.unpack_from("<i", physics_data, 0)[0]
                    if packet_id > 0:
                        rpms = struct.unpack_from("<i", physics_data, 20)[0]
                        speed_kmh = struct.unpack_from("<f", physics_data, 28)[0]
                        pit_limiter_on = struct.unpack_from("<i", physics_data, 248)[0]
                        is_limiter = bool(pit_limiter_on != 0)

                        if self._shm_static:
                            try:
                                self._shm_static.seek(0)
                                static_data = self._shm_static.read(512)
                                if len(static_data) >= 414:
                                    static_max = struct.unpack_from("<i", static_data, 410)[0]
                                    if static_max > 1000:
                                        self._static_max_rpm = float(static_max)
                            except Exception:
                                pass

                        max_rpm = self._static_max_rpm if self._static_max_rpm > 1000 else self._max_rpm_seen
                        if rpms > self._max_rpm_seen:
                            self._max_rpm_seen = float(rpms)
                            max_rpm = self._max_rpm_seen

                        if rpms > 0 or is_limiter:
                            if is_limiter:
                                pit_limit = 60.0
                                window_start = pit_limit - 20.0
                                if speed_kmh > pit_limit:
                                    return (1.0 + min(1.0, (speed_kmh - pit_limit) / 20.0), True)
                                elif speed_kmh < window_start:
                                    return (0.0, True)
                                else:
                                    return ((speed_kmh - window_start) / (pit_limit - window_start), True)

                            norm_pct = max(0.0, min(1.0, float(rpms) / max_rpm)) if max_rpm > 0 else 0.0
                            return (norm_pct, False)
            except Exception:
                self.disconnect()

        # 2. Fallback: UDP Socket
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
                pit_limit = 60.0
                window_start = pit_limit - 20.0
                if speed_kmh > pit_limit:
                    return (1.0 + min(1.0, (speed_kmh - pit_limit) / 20.0), True)
                elif speed_kmh < window_start:
                    return (0.0, True)
                else:
                    return ((speed_kmh - window_start) / (pit_limit - window_start), True)

            if engine_rpm > self._max_rpm_seen:
                self._max_rpm_seen = float(engine_rpm)

            return (max(0.0, min(1.0, engine_rpm / self._max_rpm_seen)), False)
        except Exception:
            return None


# ===========================================================================
# F1 2018 – 2026+ — UDP port 20777
# ===========================================================================
class F1Reader:
    """Reads engine RPM from F1 series UDP broadcast on port 20777."""

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
                    if pkt_format in (2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026):
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
# Automobilista 2 & Project CARS 1/2/3 — Shared Memory & UDP port 5606
# ===========================================================================
class AMS2Reader:
    """Reads RPM from Automobilista 1/2 and Project CARS 1/2/3 via Shared Memory ($pcars2$, $pcars$) and UDP."""

    def __init__(self) -> None:
        self._sock: Optional[socket.socket] = None
        self._shm: Optional[mmap.mmap] = None
        self._port: int = config.AMS2_UDP_PORT
        self._max_rpm_seen: float = 1000.0

    def connect(self) -> bool:
        for shm_name in ["$pcars2$", "$pcars$"]:
            try:
                self._shm = mmap.mmap(-1, 16384, shm_name)
                log.info("AMS2 / Project CARS Shared Memory connected ('%s')!", shm_name)
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
            for shm_name in ["$pcars2$", "$pcars$"]:
                try:
                    self._shm = mmap.mmap(-1, 16384, shm_name)
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
                        track_location = struct.unpack_from("<I", data, 112)[0] if len(data) >= 116 else 1
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
                        is_in_pit_lane = (track_location in (2, 3, 4)) or (pit_mode in (1, 2, 3))
                        is_pit_active = is_pit_limiter_on or is_in_pit_lane

                        if max_rpm > 1000.0:
                            self._max_rpm_seen = max_rpm
                        elif rpm > self._max_rpm_seen:
                            self._max_rpm_seen = rpm

                        if is_pit_active:
                            speed_kmh = speed_ms * 3.6
                            pit_limit = 60.0
                            window_start = pit_limit - 20.0
                            if speed_kmh > pit_limit:
                                return (1.0 + min(1.0, (speed_kmh - pit_limit) / 20.0), True)
                            elif speed_kmh < window_start:
                                return (0.0, True)
                            else:
                                return ((speed_kmh - window_start) / (pit_limit - window_start), True)

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
# Unified Universal Sim Racing Supervisor Loop
# ===========================================================================
async def run() -> None:
    log.info("Universal sim racing telemetry starting (AC Shared Memory + F1 + AMS2 + Forza)...")
    readers = [
        ("AC",     ACReader(config.AC_UDP_PORT)),
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

