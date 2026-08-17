"""
mod_screen_capture.py — Screen edge ambient capture for Seg 0.

Replaces Prismatik. Captures screen edges and computes average colors per
LED zone, then writes to state.seg0_colors.

Uses dxcam (DXGI Desktop Duplication API) for high performance.
Falls back to mss (GDI BitBlt) if dxcam is unavailable.

Physical LED layout (clockwise from bottom-middle, 109 LEDs):
  idx  0-17  : bottom-right (18 LEDs, center -> right corner)
  idx 18-35  : right edge   (18 LEDs, bottom-right -> top-right)
  idx 36-71  : top edge     (36 LEDs, top-right -> top-left)
  idx 72-89  : left edge    (18 LEDs, top-left -> bottom-left)
  idx 90-108 : bottom-left  (19 LEDs, bottom-left -> center)
"""

import asyncio
import logging
import time
from typing import List, Optional, Tuple

import numpy as np

import config
from state import state, Seg0Source

log = logging.getLogger("screen_capture")

RGB = Tuple[int, int, int]

# Try dxcam first, fall back to mss
try:
    import dxcam
    _USE_DXCAM = True
    log.info("Using dxcam (DXGI) for screen capture.")
except ImportError:
    _USE_DXCAM = False
    try:
        import mss
        log.info("dxcam unavailable, using mss (GDI) for screen capture.")
    except ImportError:
        log.error("Neither dxcam nor mss installed. Screen capture disabled.")


class ScreenCaptureEngine:
    """Captures screen edges and computes per-LED average colors."""

    def __init__(self) -> None:
        self._camera = None
        self._sct = None  # mss fallback
        self._monitor = None
        self._width = 0
        self._height = 0
        self._depth_h = 0  # edge depth in pixels (vertical)
        self._depth_w = 0  # edge depth in pixels (horizontal)

    def start(self) -> bool:
        """Initialize the screen capture backend."""
        if _USE_DXCAM:
            try:
                self._camera = dxcam.create(output_color="BGR")
                self._camera.start(target_fps=config.SCREEN_CAPTURE_FPS)
                # Get screen dimensions from first frame
                frame = self._camera.get_latest_frame()
                if frame is not None:
                    self._height, self._width = frame.shape[:2]
                else:
                    self._height, self._width = 1080, 1920
                log.info("dxcam started: %dx%d", self._width, self._height)
            except Exception as exc:
                log.error("dxcam init failed: %s. Falling back to mss.", exc)
                return self._start_mss()
        else:
            return self._start_mss()

        self._depth_h = int(self._height * config.SCREEN_EDGE_DEPTH_PCT)
        self._depth_w = int(self._width * config.SCREEN_EDGE_DEPTH_PCT)
        return True

    def _start_mss(self) -> bool:
        """Initialize mss fallback."""
        try:
            import mss as mss_mod
            self._sct = mss_mod.mss()
            mon = self._sct.monitors[1]  # Primary monitor
            self._width = mon["width"]
            self._height = mon["height"]
            self._monitor = mon
            self._depth_h = int(self._height * config.SCREEN_EDGE_DEPTH_PCT)
            self._depth_w = int(self._width * config.SCREEN_EDGE_DEPTH_PCT)
            log.info("mss started: %dx%d", self._width, self._height)
            return True
        except Exception as exc:
            log.error("mss init failed: %s", exc)
            return False

    def stop(self) -> None:
        """Stop capture."""
        if self._camera:
            try:
                self._camera.stop()
            except Exception:
                pass
        self._camera = None
        self._sct = None

    def capture_frame(self) -> Optional[np.ndarray]:
        """Capture one frame as a BGR numpy array. Returns None on failure."""
        if _USE_DXCAM and self._camera:
            frame = self._camera.get_latest_frame()
            return frame  # Already BGR numpy array
        elif self._sct:
            try:
                raw = self._sct.grab(self._monitor)
                # mss returns BGRA, drop alpha
                return np.array(raw, dtype=np.uint8)[:, :, :3]
            except Exception:
                return None
        return None

    def compute_edge_colors(self, frame: np.ndarray) -> List[RGB]:
        """
        Extract 109 LED colors from screen edges.

        Returns list of 109 (R, G, B) tuples matching the physical layout:
          idx  0-17  : bottom-right (left-to-right on screen bottom)
          idx 18-35  : right edge (bottom-to-top on screen right)
          idx 36-71  : top edge (right-to-left on screen top)
          idx 72-89  : left edge (top-to-bottom on screen left)
          idx 90-108 : bottom-left (right-to-left on screen bottom)
        """
        h, w = frame.shape[:2]
        dh = self._depth_h
        dw = self._depth_w

        colors: List[RGB] = []

        # --- Bottom edge (37 LEDs total: 18 right-half + 19 left-half) ---
        bottom_strip = frame[h - dh:, :]  # bottom edge
        mid = w // 2

        # Bottom-right (idx 0-17): center -> right corner (18 LEDs)
        bottom_right = bottom_strip[:, mid:]
        br_bins = np.array_split(bottom_right, 18, axis=1)
        for b in br_bins:
            avg = b.mean(axis=(0, 1)).astype(np.uint8)
            colors.append((int(avg[2]), int(avg[1]), int(avg[0])))  # BGR -> RGB

        # --- Right edge (idx 18-35): bottom -> top (18 LEDs) ---
        right_strip = frame[:, w - dw:]
        r_bins = np.array_split(right_strip, 18, axis=0)[::-1]  # bottom-to-top
        for b in r_bins:
            avg = b.mean(axis=(0, 1)).astype(np.uint8)
            colors.append((int(avg[2]), int(avg[1]), int(avg[0])))

        # --- Top edge (idx 36-71): right -> left (36 LEDs) ---
        top_strip = frame[:dh, :]
        t_bins = np.array_split(top_strip, 36, axis=1)[::-1]  # right-to-left
        for b in t_bins:
            avg = b.mean(axis=(0, 1)).astype(np.uint8)
            colors.append((int(avg[2]), int(avg[1]), int(avg[0])))

        # --- Left edge (idx 72-89): top -> bottom (18 LEDs) ---
        left_strip = frame[:, :dw]
        l_bins = np.array_split(left_strip, 18, axis=0)  # top-to-bottom
        for b in l_bins:
            avg = b.mean(axis=(0, 1)).astype(np.uint8)
            colors.append((int(avg[2]), int(avg[1]), int(avg[0])))

        # --- Bottom-left (idx 90-108): left corner -> center (19 LEDs) ---
        bottom_left = bottom_strip[:, :mid]
        bl_bins = np.array_split(bottom_left, 19, axis=1)[::-1]  # right-to-left
        for b in bl_bins:
            avg = b.mean(axis=(0, 1)).astype(np.uint8)
            colors.append((int(avg[2]), int(avg[1]), int(avg[0])))

        return colors


async def run() -> None:
    """
    Main screen capture loop. Runs until shutdown.

    Only active when state.seg0_source == SCREEN_CAPTURE.
    Other modules (Chroma, spatial telemetry) take priority.
    """
    engine = ScreenCaptureEngine()

    if not await asyncio.to_thread(engine.start):
        log.error("Screen capture failed to start.")
        return

    min_interval = 1.0 / config.SCREEN_CAPTURE_FPS
    log.info("Screen capture running at %d FPS.", config.SCREEN_CAPTURE_FPS)

    while not state.shutdown_event.is_set():
        loop_start = time.monotonic()

        # Only capture when we own Seg 0
        if state.seg0_source == Seg0Source.SCREEN_CAPTURE:
            frame = await asyncio.to_thread(engine.capture_frame)
            if frame is not None:
                colors = await asyncio.to_thread(engine.compute_edge_colors, frame)
                if len(colors) == config.SEG0_COUNT:
                    state.update_seg0_colors(colors)

        elapsed = time.monotonic() - loop_start
        sleep_for = max(0.0, min_interval - elapsed)
        if sleep_for > 0:
            await asyncio.sleep(sleep_for)

    await asyncio.to_thread(engine.stop)
    log.info("Screen capture stopped.")
