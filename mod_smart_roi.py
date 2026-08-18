"""
mod_smart_roi.py - Smart Region of Interest screen analysis.

Detects game events from screen content without telemetry:
  1. Directional Damage Detection:
     - Samples 4 inner margin strips (left, right, top, bottom)
     - When red intensity spikes on one side -> flash that side of Seg 0
     - Works universally across most FPS/action games

  2. Health Bar Monitoring (future)
  3. Minimap Analysis (future)

This module only activates when no higher-priority source owns Seg 0.

Reads frames from state.latest_frame (published by mod_screen_capture),
so NO separate screen capture backend is needed here.
"""

import asyncio
import logging
import time
from typing import List, Optional, Tuple

import numpy as np

import config
from state import state, Seg0Source

log = logging.getLogger("smart_roi")

RGB = Tuple[int, int, int]

_RED    = (255, 0, 0)
_OFF    = (0, 0, 0)


class DamageDetector:
    """
    Detects directional damage vignettes by monitoring red channel intensity
    in the inner margins of the screen.

    Receives a pre-captured BGR numpy frame — no capture backend of its own.
    """

    def analyze(self, frame: np.ndarray) -> dict:
        """
        Analyze inner margins of a BGR frame for red damage vignettes.

        Returns dict with keys: left, right, top, bottom
        Values: True if damage flash detected on that side
        """
        h, w = frame.shape[:2]
        result = {"left": False, "right": False, "top": False, "bottom": False}

        margin_x = int(w * config.ROI_DAMAGE_MARGIN_PCT)
        margin_y = int(h * config.ROI_DAMAGE_MARGIN_PCT)
        depth_x  = max(1, int(w * config.ROI_DAMAGE_DEPTH_PCT))
        depth_y  = max(1, int(h * config.ROI_DAMAGE_DEPTH_PCT))

        strips = {
            "left":   frame[:, margin_x:margin_x + depth_x],
            "right":  frame[:, w - margin_x - depth_x:w - margin_x],
            "top":    frame[margin_y:margin_y + depth_y, :],
            "bottom": frame[h - margin_y - depth_y:h - margin_y, :],
        }

        for name, strip in strips.items():
            if strip.size == 0:
                continue
            avg_b = float(strip[:, :, 0].mean())
            avg_g = float(strip[:, :, 1].mean())
            avg_r = float(strip[:, :, 2].mean())

            # Detect red damage: red channel above threshold AND
            # red dominates green and blue by ROI_RED_DOMINANCE ratio
            if (avg_r > config.ROI_RED_THRESHOLD and
                    avg_r > avg_g * config.ROI_RED_DOMINANCE and
                    avg_r > avg_b * config.ROI_RED_DOMINANCE):
                result[name] = True

        return result


def _damage_to_seg0(damage: dict) -> Optional[List[RGB]]:
    """
    Map directional damage detection to Seg 0 LED colors.

    Physical mapping:
      Left damage   -> idx 72-108 (left edge + bottom-left)
      Right damage  -> idx  0-35  (bottom-right + right edge)
      Top damage    -> idx 36-71  (top edge)
      Bottom damage -> idx  0-17 + idx 90-108 (bottom strips)
    """
    if not any(damage.values()):
        return None

    colors = [_OFF] * config.SEG0_COUNT

    if damage["right"]:
        for i in range(0, 36):
            colors[i] = _RED

    if damage["top"]:
        for i in range(36, 72):
            colors[i] = _RED

    if damage["left"]:
        for i in range(72, config.SEG0_COUNT):
            colors[i] = _RED

    if damage["bottom"]:
        for i in range(0, 18):
            colors[i] = _RED
        for i in range(90, config.SEG0_COUNT):
            colors[i] = _RED

    return colors


async def run() -> None:
    """
    Smart ROI monitor.

    Runs at 15 FPS. Only activates when Seg 0 is in SCREEN_CAPTURE or
    SMART_ROI mode (i.e., no higher-priority source like Chroma/telemetry).

    Reads frames from state.latest_frame — no separate capture backend.
    """
    detector = DamageDetector()

    min_interval = 1.0 / 15  # 15 Hz
    cooldown_end = 0.0
    log.info("Smart ROI damage detector running at 15 FPS (shared frame).")

    while not state.shutdown_event.is_set():
        loop_start = time.monotonic()

        # Only analyze when no higher-priority source owns Seg 0
        if state.seg0_source in (Seg0Source.SCREEN_CAPTURE, Seg0Source.SMART_ROI):
            frame = state.latest_frame  # lock-free read (GIL-safe numpy ref)
            if frame is not None:
                damage = await asyncio.to_thread(detector.analyze, frame)

                colors = _damage_to_seg0(damage)
                if colors:
                    state.update_seg0_colors(colors)
                    if state.seg0_source != Seg0Source.SMART_ROI:
                        await state.set_seg0_source(Seg0Source.SMART_ROI)
                    cooldown_end = time.monotonic() + 0.3  # Hold for 300ms
                elif time.monotonic() > cooldown_end:
                    # No damage detected: release back to screen capture
                    if state.seg0_source == Seg0Source.SMART_ROI:
                        await state.set_seg0_source(Seg0Source.SCREEN_CAPTURE)

        elapsed = time.monotonic() - loop_start
        sleep_for = max(0.0, min_interval - elapsed)
        if sleep_for > 0:
            await asyncio.sleep(sleep_for)

    log.info("Smart ROI stopped.")
