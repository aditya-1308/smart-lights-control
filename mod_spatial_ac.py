"""
mod_spatial_ac.py - Assetto Corsa spatial telemetry effects on Seg 0.

Monitors AC Shared Memory (acpmf_physics / acpmf_graphics) and UDP telemetry for:
  - Pit lane / pit limiter entry (calm pit blue spatial aura on Seg 0)
  - Live racing session ambient synchronization
  - Session flags (yellow / blue / checkered)
"""

import asyncio
import logging
import mmap
import socket
import struct
import time
from typing import List, Optional, Tuple

import config
from state import state, Seg0Source, AppContext

log = logging.getLogger("spatial_ac")
RGB = Tuple[int, int, int]

_PIT_BLUE: RGB = (60, 80, 120)
_YELLOW_FLAG: RGB = (255, 180, 0)
_BLUE_FLAG: RGB = (0, 100, 255)
_WHITE: RGB = (255, 255, 255)
_OFF: RGB = (0, 0, 0)


class ACSpatialReader:
    """Listens to AC Shared Memory and UDP telemetry for spatial ambient cues."""

    def __init__(self, port: int = 9996) -> None:
        self._port = port
        self._shm_physics: Optional[mmap.mmap] = None
        self._shm_graphics: Optional[mmap.mmap] = None
        self._sock: Optional[socket.socket] = None

    def connect(self) -> bool:
        self._connect_shm()
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(("0.0.0.0", 0))
            self._sock.setblocking(False)
            self._sock.sendto(struct.pack("<iii", 1, 1, 0), ("127.0.0.1", self._port))
        except OSError:
            self._sock = None

        return (self._shm_physics is not None) or (self._shm_graphics is not None) or (self._sock is not None)

    def _connect_shm(self) -> None:
        if not self._shm_physics:
            try:
                self._shm_physics = mmap.mmap(-1, 4096, "acpmf_physics")
            except Exception:
                self._shm_physics = None

        if not self._shm_graphics:
            try:
                self._shm_graphics = mmap.mmap(-1, 4096, "acpmf_graphics")
            except Exception:
                self._shm_graphics = None

    def disconnect(self) -> None:
        if self._shm_physics:
            try:
                self._shm_physics.close()
            except Exception:
                pass
            self._shm_physics = None

        if self._shm_graphics:
            try:
                self._shm_graphics.close()
            except Exception:
                pass
            self._shm_graphics = None

        if self._sock:
            try:
                self._sock.sendto(struct.pack("<iii", 1, 1, 2), ("127.0.0.1", self._port))
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def read_event(self) -> Optional[str]:
        # 1. Primary: Windows Shared Memory
        if not self._shm_physics or not self._shm_graphics:
            self._connect_shm()

        if self._shm_physics:
            try:
                self._shm_physics.seek(0)
                p_data = self._shm_physics.read(256)
                if len(p_data) >= 252:
                    p_id = struct.unpack_from("<i", p_data, 0)[0]
                    if p_id > 0:
                        rpms = struct.unpack_from("<i", p_data, 20)[0]
                        pit_limiter = struct.unpack_from("<i", p_data, 248)[0]

                        # Check graphics block for flags / pit status
                        is_in_pit = 0
                        if self._shm_graphics:
                            try:
                                self._shm_graphics.seek(0)
                                g_data = self._shm_graphics.read(1240)
                                if len(g_data) >= 1236:
                                    is_in_pit = struct.unpack_from("<i", g_data, 160)[0]
                                    is_in_pit_lane = struct.unpack_from("<i", g_data, 1232)[0]
                                    if is_in_pit or is_in_pit_lane:
                                        return "pit"
                            except Exception:
                                pass

                        if pit_limiter or is_in_pit:
                            return "pit"
                        if rpms > 0:
                            return "racing"
            except Exception:
                self.disconnect()

        # 2. Fallback: UDP stream
        if not self._sock:
            return None
        try:
            data, _ = self._sock.recvfrom(2048)
            if len(data) >= 30:
                is_in_pit = data[28]
                if is_in_pit:
                    return "pit"
                return "racing"
        except (BlockingIOError, OSError):
            pass

        return None


async def run() -> None:
    """AC spatial telemetry monitor loop."""
    log.info("AC spatial telemetry monitor starting (Shared Memory + UDP)...")
    reader = ACSpatialReader(config.AC_UDP_PORT)

    await asyncio.to_thread(reader.connect)
    last_handshake = 0.0

    while not state.shutdown_event.is_set():
        now = time.monotonic()
        if now - last_handshake > 3.0 and reader._sock:
            await asyncio.to_thread(
                lambda: reader._sock.sendto(struct.pack("<iii", 1, 1, 0), ("127.0.0.1", config.AC_UDP_PORT))
                if reader._sock else None
            )
            last_handshake = now

        event = await asyncio.to_thread(reader.read_event)
        if event == "pit":
            await state.set_context(AppContext.RACING)
            state.update_seg0_colors([_PIT_BLUE] * config.SEG0_COUNT)
            if state.seg0_source != Seg0Source.AC_SPATIAL:
                await state.set_seg0_source(Seg0Source.AC_SPATIAL)
        elif event == "racing":
            await state.set_context(AppContext.RACING)
            if state.seg0_source == Seg0Source.AC_SPATIAL:
                await state.set_seg0_source(Seg0Source.SCREEN_CAPTURE)
        else:
            if state.seg0_source == Seg0Source.AC_SPATIAL:
                await state.set_seg0_source(Seg0Source.SCREEN_CAPTURE)

        await asyncio.sleep(0.05)

    await asyncio.to_thread(reader.disconnect)
    log.info("AC spatial module stopped.")

