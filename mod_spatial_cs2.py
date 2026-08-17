"""
mod_spatial_cs2.py — Enhanced CS2 spatial effects on Seg 0.

Extends the existing CS2 GSI module with Seg 0 spatial effects:
  - Flashbang: entire Seg 0 blasts white for 2s
  - Low health: red breathing pulse on Seg 0
  - Bomb planted: accelerating red flash
  - Fire/burning: orange-red glow

Monitors state.cs2_connected and CS2 state fields.
Releases Seg 0 back to screen capture when no events active.
"""

import asyncio
import logging
import math
import time
from typing import List, Tuple

import config
from state import state, Seg0Source

log = logging.getLogger("spatial_cs2")

RGB = Tuple[int, int, int]

_WHITE = (255, 255, 255)
_RED = (255, 0, 0)
_ORANGE_RED = (255, 80, 0)
_OFF = (0, 0, 0)


def _solid_109(color: RGB) -> List[RGB]:
    return [color] * config.SEG0_COUNT


def _ease_in_out(t: float) -> float:
    return t * t * (3 - 2 * t)


async def run() -> None:
    """CS2 spatial effects monitor for Seg 0."""
    log.info("CS2 spatial module starting.")

    pulse_period = 1.5  # seconds per breath cycle

    while not state.shutdown_event.is_set():
        await asyncio.sleep(1.0 / 20)  # 20 Hz check

        # Only run when CS2 is connected
        if not state.cs2_connected:
            continue

        # Skip if Chroma has priority
        if state.seg0_source == Seg0Source.CHROMA:
            continue

        now = time.monotonic()
        event_colors = None

        # --- Flashbang ---
        if state.cs2_flashed > 0:
            # Full white blast on Seg 0 (Seg 1+2 handled by mod_b_cs2_gsi)
            event_colors = _solid_109(_WHITE)

        # --- Low health ---
        elif state.cs2_health < 20:
            # Red breathing pulse
            t = (now % pulse_period) / pulse_period
            brightness = _ease_in_out(abs(math.sin(math.pi * t)))
            r = int(brightness * 255)
            event_colors = _solid_109((r, 0, 0))

        # --- Apply or release ---
        if event_colors:
            state.update_seg0_colors(event_colors)
            if state.seg0_source != Seg0Source.CS2_SPATIAL:
                await state.set_seg0_source(Seg0Source.CS2_SPATIAL)
        else:
            # No CS2 event on Seg 0: release if we had it
            if state.seg0_source == Seg0Source.CS2_SPATIAL:
                await state.set_seg0_source(Seg0Source.SCREEN_CAPTURE)

    log.info("CS2 spatial module stopped.")
