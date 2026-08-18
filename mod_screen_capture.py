"""
mod_screen_capture.py - Realtime screen edge ambient capture for Segment 0.

Directly imports and uses the exact 109 LED zone coordinates and gamma
curve from the user's calibrated Prismatik profile (Movies.ini / Lightpack.ini).

Architecture:
  - Loads 109 exact (x, y, width, height) sampling rectangles from Prismatik profile.
  - Slices only those 109 zones from the high-speed DXcam frame.
  - Averages each zone and applies Prismatik's gamma curve (Gamma=2.004).
  - Streams 109-LED DNRGB UDP packets directly to WLED Port 21324.
  - WLED routes incoming UDP frames strictly to Main Segment (Segment 0).
"""

import asyncio
import logging
import os
import re
import socket
import time
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

import config
from state import state, Seg0Source

log = logging.getLogger("screen_capture")
RGB = Tuple[int, int, int]

_UDP_PORT = 21324
_REALTIME_TIMEOUT = 2  # seconds

try:
    import dxcam
    _USE_DXCAM = True
except ImportError:
    _USE_DXCAM = False


def _load_prismatik_profile() -> Optional[List[Tuple[slice, slice]]]:
    """
    Load exact LED zone rectangles from the active Prismatik profile.
    Reads C:\\Users\\<User>\\Prismatik\\Profiles\\Movies.ini or Lightpack.ini.
    """
    prismatik_dir = Path(os.path.expanduser("~")) / "Prismatik"
    main_conf = prismatik_dir / "main.conf"
    profile_name = "Movies"

    if main_conf.exists():
        try:
            for line in main_conf.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("ProfileLast="):
                    profile_name = line.split("=", 1)[1].strip()
                    break
        except Exception:
            pass

    candidates = [
        prismatik_dir / "Profiles" / f"{profile_name}.ini",
        prismatik_dir / "Profiles" / "Movies.ini",
        prismatik_dir / "Profiles" / "Lightpack.ini",
    ]

    for ini_path in candidates:
        if not ini_path.exists():
            continue
        try:
            text = ini_path.read_text(encoding="utf-8", errors="ignore")
            slices = []
            for i in range(1, config.SEG0_COUNT + 1):
                sec = f"[LED_{i}]"
                start = text.find(sec)
                if start == -1:
                    break
                end = text.find("\n[", start + 1)
                block = text[start:end] if end != -1 else text[start:]

                pos_m  = re.search(r"Position=@Point\((\d+)\s+(\d+)\)", block)
                size_m = re.search(r"Size=@Size\((\d+)\s+(\d+)\)", block)
                if not (pos_m and size_m):
                    break

                x, y = int(pos_m.group(1)), int(pos_m.group(2))
                w, h = int(size_m.group(1)), int(size_m.group(2))
                slices.append((slice(y, y + h), slice(x, x + w)))

            if len(slices) == config.SEG0_COUNT:
                log.info("Successfully loaded %d exact LED zones from Prismatik profile: %s",
                         len(slices), ini_path.name)
                return slices
        except Exception as exc:
            log.warning("Could not parse %s: %s", ini_path, exc)

    return None


def _compute_fallback_slices(h: int, w: int) -> list:
    """Fallback geometric zones if Prismatik profile is not found."""
    y_top_start   = int(h * 0.02)
    y_top_end     = int(h * 0.12)
    y_bot_start   = int(h * 0.88)
    y_bot_end     = int(h * 0.98)
    x_left_start  = int(w * 0.02)
    x_left_end    = int(w * 0.12)
    x_right_start = int(w * 0.88)
    x_right_end   = int(w * 0.98)

    mid = w // 2
    slices = []

    def sy(a, b): return slice(max(0, min(h, a)), max(a + 1, min(h, b)))
    def sx(a, b): return slice(max(0, min(w, a)), max(a + 1, min(w, b)))

    # 1. Bottom-right (0..17, 18 LEDs): center -> right
    for i in range(18):
        x1 = mid + (w - mid) * i // 18
        x2 = mid + (w - mid) * (i + 1) // 18
        slices.append((sy(y_bot_start, y_bot_end), sx(x1, x2)))

    # 2. Right edge (18..35, 18 LEDs): bottom -> top
    for i in range(18):
        y2 = h - h * i // 18
        y1 = h - h * (i + 1) // 18
        slices.append((sy(y1, y2), sx(x_right_start, x_right_end)))

    # 3. Top edge (36..71, 36 LEDs): right -> left
    for i in range(36):
        x2 = w - w * i // 36
        x1 = w - w * (i + 1) // 36
        slices.append((sy(y_top_start, y_top_end), sx(x1, x2)))

    # 4. Left edge (72..89, 18 LEDs): top -> bottom
    for i in range(18):
        y1 = h * i // 18
        y2 = h * (i + 1) // 18
        slices.append((sy(y1, y2), sx(x_left_start, x_left_end)))

    # 5. Bottom-left (90..108, 19 LEDs): left -> center
    for i in range(19):
        x1 = mid * i // 19
        x2 = mid * (i + 1) // 19
        slices.append((sy(y_bot_start, y_bot_end), sx(x1, x2)))

    return slices


class ScreenCaptureEngine:
    """
    Captures screen frames and streams DNRGB UDP packets to WLED Segment 0.
    """

    def __init__(self) -> None:
        self._camera = None
        self._sct = None
        self._monitor = None
        self._width = 0
        self._height = 0
        self._led_slices: list = []
        self._sock: Optional[socket.socket] = None

    def start(self) -> bool:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)

        if _USE_DXCAM:
            try:
                self._camera = dxcam.create(output_color="BGR")
                self._camera.start(target_fps=config.SCREEN_CAPTURE_FPS)
                for _ in range(30):
                    frame = self._camera.get_latest_frame()
                    if frame is not None:
                        self._height, self._width = frame.shape[:2]
                        break
                    time.sleep(0.033)
                else:
                    self._height, self._width = 1080, 1920
                log.info("dxcam started: %dx%d", self._width, self._height)
            except Exception as exc:
                log.warning("dxcam failed (%s). Falling back to mss.", exc)
                return self._start_mss()
        else:
            return self._start_mss()

        self._init_regions()
        return True

    def _start_mss(self) -> bool:
        try:
            import mss as mss_mod
            self._sct = mss_mod.mss()
            mon = self._sct.monitors[1]
            self._width  = mon["width"]
            self._height = mon["height"]
            self._monitor = mon
            self._init_regions()
            log.info("mss started: %dx%d", self._width, self._height)
            return True
        except Exception as exc:
            log.error("mss also failed: %s", exc)
            return False

    def _init_regions(self) -> None:
        # 1. Try loading user's exact Prismatik calibrated profile
        loaded_slices = _load_prismatik_profile()
        if loaded_slices is not None and len(loaded_slices) == config.SEG0_COUNT:
            self._led_slices = loaded_slices
        else:
            log.info("Prismatik profile not found — using fallback geometric zones.")
            self._led_slices = _compute_fallback_slices(self._height, self._width)

        log.info("Segment 0 capture ready: %d zones active.", len(self._led_slices))

    def stop(self) -> None:
        if self._camera:
            try:
                self._camera.stop()
            except Exception:
                pass
            self._camera = None
        if self._sct:
            try:
                self._sct.close()
            except Exception:
                pass
            self._sct = None
        if self._sock:
            self._sock.close()
            self._sock = None

    def capture_frame(self) -> Optional[np.ndarray]:
        frame = None
        if self._camera is not None:
            frame = self._camera.get_latest_frame()
        elif self._sct is not None:
            try:
                raw = self._sct.grab(self._monitor)
                frame = np.array(raw, dtype=np.uint8)[:, :, :3]
            except Exception:
                frame = None

        if frame is not None:
            state.latest_frame = frame
        return frame

    def compute_edge_colors(self, frame: np.ndarray) -> List[RGB]:
        """
        Extract 109 LED colors using Prismatik's exact zones and gamma curve.
        """
        n_leds = len(self._led_slices)
        h, w = frame.shape[:2]
        raw_bgr = np.zeros((n_leds, 3), dtype=np.float32)

        for idx, (row_sl, col_sl) in enumerate(self._led_slices):
            # Clamp slice bounds to current frame dimensions
            r_start = max(0, min(h, row_sl.start or 0))
            r_stop  = max(r_start + 1, min(h, row_sl.stop or h))
            c_start = max(0, min(w, col_sl.start or 0))
            c_stop  = max(c_start + 1, min(w, col_sl.stop or w))

            region = frame[r_start:r_stop, c_start:c_stop]
            if region.size > 0:
                raw_bgr[idx] = region.mean(axis=(0, 1))

        # Convert BGR -> RGB
        raw_rgb = raw_bgr[:, [2, 1, 0]]

        # Prismatik color pipeline:
        # 1. Normalize to 0.0 - 1.0
        norm = np.clip(raw_rgb / 255.0, 0.0, 1.0)

        # 2. Saturation enhancement (1.2x)
        max_c = norm.max(axis=1, keepdims=True)
        sat_boost = max_c - (max_c - norm) * 1.2
        norm = np.clip(sat_boost, 0.0, 1.0)

        # 3. Prismatik hardware Gamma (2.004)
        norm **= 2.004

        out_rgb = (norm * 255.0).astype(np.uint8)
        return [tuple(map(int, row)) for row in out_rgb]

    def send_udp_packet(self, colors: List[RGB]) -> None:
        """
        Send DNRGB UDP packet to WLED Port 21324.
        WLED routes this directly to Main Segment (Segment 0).
        """
        if not self._sock:
            return
        header = bytes([0x04, _REALTIME_TIMEOUT, 0x00, 0x00])
        body   = bytes([ch for r, g, b in colors for ch in (r, g, b)])
        packet = header + body
        try:
            self._sock.sendto(packet, (config.WLED_IP, _UDP_PORT))
        except (BlockingIOError, OSError):
            pass


# ---------------------------------------------------------------------------
# Async run loop
# ---------------------------------------------------------------------------

async def run() -> None:
    engine = ScreenCaptureEngine()

    if not await asyncio.to_thread(engine.start):
        log.error("Screen capture failed to start.")
        return

    min_interval = 1.0 / config.SCREEN_CAPTURE_FPS
    log.info("Screen capture running at %d FPS using Prismatik profile.",
             config.SCREEN_CAPTURE_FPS)

    while not state.shutdown_event.is_set():
        loop_start = time.monotonic()

        if state.seg0_source == Seg0Source.SCREEN_CAPTURE:
            frame = await asyncio.to_thread(engine.capture_frame)
            if frame is not None:
                colors = await asyncio.to_thread(engine.compute_edge_colors, frame)
                if len(colors) == config.SEG0_COUNT:
                    state.update_seg0_colors(colors)
                    engine.send_udp_packet(colors)

        elapsed   = time.monotonic() - loop_start
        sleep_for = max(0.0, min_interval - elapsed)
        if sleep_for > 0:
            await asyncio.sleep(sleep_for)

    await asyncio.to_thread(engine.stop)
    log.info("Screen capture stopped.")
