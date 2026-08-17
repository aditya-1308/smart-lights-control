"""
mod_smart_roi.py — Smart Region of Interest screen analysis.

Captures specific screen regions to detect game events without telemetry:
  1. Directional Damage Detection:
     - Samples 4 inner margin strips (left, right, top, bottom)
     - When red intensity spikes on one side -> flash that side of Seg 0
     - Works universally across most FPS/action games

  2. Health Bar Monitoring (future):
     - Sample a configurable health bar zone
     - Map remaining health to strip brightness

  3. Minimap Analysis (future):
     - Detect bright dots on minimap, map angles to strip positions

This module only activates when no higher-priority source owns Seg 0.
It acts as an intelligent upgrade over plain screen-edge capture.

Uses dxcam or mss for capture (same backend as mod_screen_capture).
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

# Try dxcam first
try:
    import dxcam
    _USE_DXCAM = True
except ImportError:
    _USE_DXCAM = False
    try:
        import mss
    except ImportError:
        pass

_RED = (255, 0, 0)
_ORANGE = (255, 120, 0)
_OFF = (0, 0, 0)


class DamageDetector:
    """
    Detects directional damage vignettes by monitoring red channel intensity
    in the inner margins of the screen.

    Most FPS and action-adventure games display a red flash/vignette on the
    side of the screen where damage comes from. This detector catches that.
    """

    def __init__(self) -> None:
        self._camera = None
        self._sct = None
        self._monitor = None
        self._width = 0
        self._height = 0

        # Margin zones (pixels)
        self._margin_x = 0  # horizontal margin from edge
        self._margin_y = 0
        self._depth_x = 0   # depth of detection strip
        self._depth_y = 0

        # Baseline red levels per side (adaptive threshold)
        self._baseline_left = 0.0
        self._baseline_right = 0.0
        self._baseline_top = 0.0
        self._baseline_bottom = 0.0
        self._baseline_samples = 0

    def start(self) -> bool:
        """Initialize screen capture for ROI analysis."""
        if _USE_DXCAM:
            try:
                self._camera = dxcam.create(output_color="BGR")
                frame = self._camera.grab()
                if frame is not None:
                    self._height, self._width = frame.shape[:2]
                else:
                    self._height, self._width = 1080, 1920
            except Exception as exc:
                log.warning("dxcam failed for ROI: %s. Trying mss.", exc)
                return self._start_mss()
        else:
            return self._start_mss()

        self._calc_margins()
        return True

    def _start_mss(self) -> bool:
        try:
            import mss as mss_mod
            self._sct = mss_mod.mss()
            mon = self._sct.monitors[1]
            self._width = mon["width"]
            self._height = mon["height"]
            self._monitor = mon
            self._calc_margins()
            return True
        except Exception as exc:
            log.error("mss failed for ROI: %s", exc)
            return False

    def _calc_margins(self) -> None:
        self._margin_x = int(self._width * config.ROI_DAMAGE_MARGIN_PCT)
        self._margin_y = int(self._height * config.ROI_DAMAGE_MARGIN_PCT)
        self._depth_x = int(self._width * config.ROI_DAMAGE_DEPTH_PCT)
        self._depth_y = int(self._height * config.ROI_DAMAGE_DEPTH_PCT)

    def stop(self) -> None:
        self._camera = None
        self._sct = None

    def analyze(self) -> dict:
        """
        Capture screen and analyze inner margins for red damage vignettes.

        Returns dict with keys: left, right, top, bottom
        Values: True if damage flash detected on that side
        """
        frame = self._grab_frame()
        if frame is None:
            return {"left": False, "right": False, "top": False, "bottom": False}

        h, w = frame.shape[:2]
        result = {"left": False, "right": False, "top": False, "bottom": False}

        # Extract inner margin strips
        # Left strip: x from margin to margin+depth, full height
        left_strip = frame[:, self._margin_x:self._margin_x + self._depth_x]
        # Right strip: x from (w - margin - depth) to (w - margin)
        right_strip = frame[:, w - self._margin_x - self._depth_x:w - self._margin_x]
        # Top strip
        top_strip = frame[self._margin_y:self._margin_y + self._depth_y, :]
        # Bottom strip
        bot_strip = frame[h - self._margin_y - self._depth_y:h - self._margin_y, :]

        # Analyze each strip for red dominance
        # Frame is BGR format
        for name, strip in [("left", left_strip), ("right", right_strip),
                             ("top", top_strip), ("bottom", bot_strip)]:
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

    def _grab_frame(self) -> Optional[np.ndarray]:
        if _USE_DXCAM and self._camera:
            return self._camera.grab()
        elif self._sct:
            try:
                raw = self._sct.grab(self._monitor)
                return np.array(raw, dtype=np.uint8)[:, :, :3]
            except Exception:
                return None
        return None


def _damage_to_seg0(damage: dict) -> Optional[List[RGB]]:
    """
    Map directional damage detection to Seg 0 LED colors.

    Physical mapping:
      Left damage  -> idx 72-108 (left edge + bottom-left)
      Right damage -> idx 0-35 (bottom-right + right edge)
      Top damage   -> idx 36-71 (top edge)
      Bottom damage-> idx 0-17 + idx 90-108 (bottom strips)
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

    Runs at 15 FPS (lower than screen capture since it's heavier analysis).
    Only activates when Seg 0 is in SCREEN_CAPTURE mode or SMART_ROI mode
    (i.e., no higher-priority source like Chroma/telemetry).
    """
    detector = DamageDetector()

    if not await asyncio.to_thread(detector.start):
        log.error("Smart ROI failed to start.")
        return

    min_interval = 1.0 / 15  # 15 Hz
    cooldown_end = 0.0
    log.info("Smart ROI damage detector running at 15 FPS.")

    while not state.shutdown_event.is_set():
        loop_start = time.monotonic()

        # Only analyze when no higher-priority source owns Seg 0
        if state.seg0_source in (Seg0Source.SCREEN_CAPTURE, Seg0Source.SMART_ROI):
            damage = await asyncio.to_thread(detector.analyze)

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

    await asyncio.to_thread(detector.stop)
    log.info("Smart ROI stopped.")
