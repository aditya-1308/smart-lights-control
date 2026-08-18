"""
mod_spatial_ac.py - Assetto Corsa spatial telemetry effects on Seg 0 via UDP.

Monitors AC UDP telemetry stream (Port 9996) for:
  - Pit lane status
  - Engine limiter / RPM events
  - Telemetry active status

Zero shared memory / mmap — 100% crash-safe for Assetto Corsa & CSP.
"""

import asyncio
import logging
import socket
import struct
import time
from typing import List, Optional, Tuple

import config
from state import state, Seg0Source, AppContext

log = logging.getLogger("spatial_ac")
RGB = Tuple[int, int, int]

_PIT_BLUE = (60, 80, 120)
_RED = (255, 0, 0)
_OFF = (0, 0, 0)


class ACSpatialUDP:
    """Listens to AC UDP telemetry stream for spatial ambient cues."""

    def __init__(self, port: int = 9996) -> None:
        self._port = port
        self._sock: Optional[socket.socket] = None

    def connect(self) -> bool:
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(("0.0.0.0", 0))
            self._sock.setblocking(False)
            # Subscribe to telemetry
            self._sock.sendto(struct.pack("<iii", 1, 1, 0), ("127.0.0.1", self._port))
            return True
        except OSError:
            return False

    def disconnect(self) -> None:
        if self._sock:
            try:
                self._sock.sendto(struct.pack("<iii", 1, 1, 2), ("127.0.0.1", self._port))
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def read_event(self) -> Optional[str]:
        if not self._sock:
            return None
        try:
            data, _ = self._sock.recvfrom(2048)
        except (BlockingIOError, OSError):
            return None

        if len(data) < 30:
            return None

        is_in_pit = data[28]
        if is_in_pit:
            return "pit"
        return "racing"


async def run() -> None:
    """AC spatial telemetry monitor loop."""
    log.info("AC spatial UDP monitor starting.")
    reader = ACSpatialUDP(config.AC_UDP_PORT)

    if not await asyncio.to_thread(reader.connect):
        log.warning("AC spatial UDP listener could not bind.")
        return

    last_handshake = 0.0

    while not state.shutdown_event.is_set():
        now = time.monotonic()
        if now - last_handshake > 3.0:
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
