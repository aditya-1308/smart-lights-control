"""
mod_seg0_router.py - Seg 0 ownership router.

Reads state.seg0_colors (109-LED frame buffer) and sends it to WLED.
Automatic priority-based source selection:
  1. Chroma bridge (game RGB data)
  2. AC/F1 spatial telemetry events
  3. CS2 spatial effects
  4. Smart ROI (directional damage detection)
  5. Screen capture (idle fallback)

Source modules write to state.seg0_colors and set state.seg0_source.
This router simply sends whatever is in the buffer to WLED at the
configured FPS, respecting ownership priority.
"""

import asyncio
import logging
import time

import config
from state import state, Seg0Source
from wled_api import WLEDClient

log = logging.getLogger("seg0_router")

# Priority order (highest first)
_PRIORITY = [
    Seg0Source.CHROMA,
    Seg0Source.AC_SPATIAL,
    Seg0Source.F1_SPATIAL,
    Seg0Source.CS2_SPATIAL,
    Seg0Source.SMART_ROI,
    Seg0Source.SCREEN_CAPTURE,
]


async def run(wled: WLEDClient) -> None:
    """
    Main router loop: sends state.seg0_colors to WLED at configured FPS.

    Only sends when the buffer is marked dirty (state.seg0_dirty).
    """
    min_interval = 1.0 / config.SCREEN_CAPTURE_FPS
    log.info("Seg 0 router started (target %d FPS).", config.SCREEN_CAPTURE_FPS)

    while not state.shutdown_event.is_set():
        loop_start = time.monotonic()

        if state.seg0_dirty:
            colors = state.seg0_colors[:]
            state.seg0_dirty = False

            if len(colors) == config.SEG0_COUNT:
                await wled.set_seg0(colors)

        elapsed = time.monotonic() - loop_start
        sleep_for = max(0.0, min_interval - elapsed)
        if sleep_for > 0:
            await asyncio.sleep(sleep_for)

    # Shutdown: turn off Seg 0
    await wled.set_seg0_off()
    log.info("Seg 0 router stopped.")
