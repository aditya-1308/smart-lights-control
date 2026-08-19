"""
mod_screen_capture.py - Low-Latency Realtime Screen Ambient Capture for Segment 0.

Architecture:
  - Runs a dedicated background thread for capture + processing (no asyncio.to_thread overhead).
  - DXcam with max_buffer_len=1 (zero queue backlog, frames always fresh).
  - Windows High-Precision Timer (timeBeginPeriod(1)) for 1ms scheduler resolution.
  - Prismatik calibration profile for exact zone coordinates.
  - Vectorized NumPy color averaging + hardware gamma correction.
  - Non-blocking UDP DNRGB packets to WLED Port 21324.
  - Sends keepalive UDP packets when no frame is available to prevent WLED reverting to defaults.
"""

import asyncio
import ctypes
import logging
import os
import re
import socket
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

import config
from state import state, Seg0Source

log = logging.getLogger("screen_capture")
RGB = Tuple[int, int, int]

_UDP_PORT = 21324
# 5-second timeout: WLED holds realtime mode this long after the last packet.
# Must be > 1/FPS gap to prevent WLED reverting to stored effect between frames.
_REALTIME_TIMEOUT_SEC = 5

# Enable Windows 1ms high-precision scheduler
try:
    ctypes.windll.winmm.timeBeginPeriod(1)
except Exception:
    pass

try:
    import dxcam
    _USE_DXCAM = True
except ImportError:
    _USE_DXCAM = False


# ---------------------------------------------------------------------------
# Prismatik profile loader
# ---------------------------------------------------------------------------
def _load_prismatik_profile() -> Optional[List[Tuple[int, int, int, int]]]:
    """
    Load exact LED zone rectangles from active Prismatik profile.
    Returns list of (y1, y2, x1, x2) integer bounds, length == SEG0_COUNT.
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
            zones = []
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
                zones.append((y, y + h, x, x + w))

            if len(zones) == config.SEG0_COUNT:
                log.info("Loaded %d exact LED zones from Prismatik: %s",
                         len(zones), ini_path.name)
                return zones
        except Exception as exc:
            log.warning("Could not parse %s: %s", ini_path, exc)

    return None


def _compute_fallback_zones(h: int, w: int) -> List[Tuple[int, int, int, int]]:
    """Fallback geometric zones if profile not found."""
    y_top_start, y_top_end     = int(h * 0.02), int(h * 0.12)
    y_bot_start, y_bot_end     = int(h * 0.88), int(h * 0.98)
    x_left_start, x_left_end   = int(w * 0.02), int(w * 0.12)
    x_right_start, x_right_end = int(w * 0.88), int(w * 0.98)
    mid = w // 2
    zones = []

    for i in range(18):
        zones.append((y_bot_start, y_bot_end,
                      mid + (w - mid) * i // 18,
                      mid + (w - mid) * (i + 1) // 18))
    for i in range(18):
        zones.append((h - h * (i + 1) // 18, h - h * i // 18,
                      x_right_start, x_right_end))
    for i in range(36):
        zones.append((y_top_start, y_top_end,
                      w - w * (i + 1) // 36, w - w * i // 36))
    for i in range(18):
        zones.append((h * i // 18, h * (i + 1) // 18,
                      x_left_start, x_left_end))
    for i in range(19):
        zones.append((y_bot_start, y_bot_end,
                      mid * i // 19, mid * (i + 1) // 19))
    return zones


# ---------------------------------------------------------------------------
# Screen capture engine (runs in a dedicated thread)
# ---------------------------------------------------------------------------
class ScreenCaptureEngine:
    """
    Dedicated-thread screen capture engine.

    Runs a tight native loop in its own thread instead of asyncio.to_thread
    to eliminate per-frame executor scheduling overhead (~10-20ms).
    """

    def __init__(self) -> None:
        self._camera = None
        self._sct = None
        self._monitor = None
        self._width = 0
        self._height = 0
        self._zones: List[Tuple[int, int, int, int]] = []
        self._sock: Optional[socket.socket] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Pre-built UDP header for DNRGB protocol:
        # [0x04=DNRGB, timeout, start_high, start_low]
        self._header = bytes([0x04, _REALTIME_TIMEOUT_SEC, 0x00, 0x00])

        # Pre-built black keepalive packet (prevents WLED reverting to stored effect)
        self._keepalive_pkt = self._header + bytes(config.SEG0_COUNT * 3)

    # ------------------------------------------------------------------
    def start(self) -> bool:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)

        if _USE_DXCAM:
            try:
                # max_buffer_len=1 → always get the freshest frame, zero queue lag
                self._camera = dxcam.create(output_color="BGR", max_buffer_len=1)
                self._camera.start(target_fps=config.SCREEN_CAPTURE_FPS,
                                   video_mode=False)
                # Wait for first frame to get actual resolution
                for _ in range(60):
                    frame = self._camera.get_latest_frame()
                    if frame is not None:
                        self._height, self._width = frame.shape[:2]
                        break
                    time.sleep(0.016)
                else:
                    self._height, self._width = 1080, 1920
                log.info("DXcam started @ %d FPS (max_buffer_len=1): %dx%d",
                         config.SCREEN_CAPTURE_FPS, self._width, self._height)
            except Exception as exc:
                log.warning("DXcam failed (%s). Falling back to mss.", exc)
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
            log.info("mss fallback started: %dx%d", self._width, self._height)
            return True
        except Exception as exc:
            log.error("mss failed: %s", exc)
            return False

    def _init_regions(self) -> None:
        loaded = _load_prismatik_profile()
        if loaded is not None and len(loaded) == config.SEG0_COUNT:
            self._zones = loaded
        else:
            log.warning("Prismatik profile not found or wrong zone count — using fallback.")
            self._zones = _compute_fallback_zones(self._height, self._width)
        log.info("Screen capture: %d zones configured.", len(self._zones))

    # ------------------------------------------------------------------
    def stop(self) -> None:
        self._running = False
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

    # ------------------------------------------------------------------
    def _capture_frame(self) -> Optional[np.ndarray]:
        """Grab latest screen frame (blocking, called from capture thread)."""
        if self._camera is not None:
            return self._camera.get_latest_frame()
        if self._sct is not None:
            try:
                raw = self._sct.grab(self._monitor)
                return np.array(raw, dtype=np.uint8)[:, :, :3]
            except Exception:
                return None
        return None

    def _compute_colors(self, frame: np.ndarray) -> np.ndarray:
        """
        Average each zone region and apply gamma + saturation.
        Returns uint8 array of shape (SEG0_COUNT, 3) in RGB order.
        """
        n = len(self._zones)
        h, w = frame.shape[:2]
        raw = np.empty((n, 3), dtype=np.float32)

        for idx, (y1, y2, x1, x2) in enumerate(self._zones):
            r1 = max(0, min(h, y1));  r2 = max(r1 + 1, min(h, y2))
            c1 = max(0, min(w, x1));  c2 = max(c1 + 1, min(w, x2))
            region = frame[r1:r2, c1:c2]
            if region.size > 0:
                raw[idx] = region.mean(axis=(0, 1))
            else:
                raw[idx] = 0.0

        # BGR → RGB
        rgb = raw[:, [2, 1, 0]] / 255.0

        # Saturation boost
        max_c = rgb.max(axis=1, keepdims=True)
        rgb = np.clip(max_c - (max_c - rgb) * config.SCREEN_CAPTURE_SATURATION, 0.0, 1.0)

        # Gamma curve
        rgb = np.power(rgb, config.SCREEN_CAPTURE_GAMMA)

        return (rgb * 255.0).astype(np.uint8)

    def _send_colors(self, colors_u8: np.ndarray) -> None:
        """Fire DNRGB UDP packet (non-blocking, fire-and-forget)."""
        if not self._sock:
            return
        body = colors_u8.tobytes()
        try:
            self._sock.sendto(self._header + body, (config.WLED_IP, _UDP_PORT))
        except (BlockingIOError, OSError):
            pass

    def _send_keepalive(self) -> None:
        """Send a keepalive packet to hold WLED in realtime mode."""
        if not self._sock:
            return
        try:
            self._sock.sendto(self._keepalive_pkt, (config.WLED_IP, _UDP_PORT))
        except (BlockingIOError, OSError):
            pass

    # ------------------------------------------------------------------
    def run_loop(self) -> None:
        """
        Main capture loop — runs in dedicated thread.
        Tight native loop with no asyncio overhead.
        """
        interval = 1.0 / config.SCREEN_CAPTURE_FPS
        keepalive_interval = _REALTIME_TIMEOUT_SEC / 2.0
        last_keepalive = 0.0
        self._running = True

        log.info("Screen capture thread started @ %d FPS.", config.SCREEN_CAPTURE_FPS)

        while self._running:
            t0 = time.monotonic()

            if state.seg0_source == Seg0Source.SCREEN_CAPTURE:
                frame = self._capture_frame()
                if frame is not None:
                    state.latest_frame = frame
                    colors_u8 = self._compute_colors(frame)
                    state.update_seg0_colors(
                        [tuple(map(int, row)) for row in colors_u8]
                    )
                    self._send_colors(colors_u8)
                    last_keepalive = t0
                else:
                    # No frame yet — send keepalive so WLED doesn't revert
                    if t0 - last_keepalive >= keepalive_interval:
                        self._send_keepalive()
                        last_keepalive = t0
            else:
                # Seg 0 owned by another source — send keepalive periodically
                # so that when ownership returns the strip is still in realtime mode
                if t0 - last_keepalive >= keepalive_interval:
                    self._send_keepalive()
                    last_keepalive = t0

            elapsed = time.monotonic() - t0
            sleep = interval - elapsed
            if sleep > 0.001:
                time.sleep(sleep)


# ---------------------------------------------------------------------------
# Async entry point
# ---------------------------------------------------------------------------
async def run() -> None:
    engine = ScreenCaptureEngine()

    if not await asyncio.to_thread(engine.start):
        log.error("Screen capture failed to start.")
        return

    # Launch the tight capture loop in its own thread
    thread = threading.Thread(target=engine.run_loop, name="screen_capture_loop",
                              daemon=True)
    thread.start()

    # Wait for shutdown signal, then stop
    await state.shutdown_event.wait()
    engine.stop()
    thread.join(timeout=2.0)
    log.info("Screen capture stopped.")
