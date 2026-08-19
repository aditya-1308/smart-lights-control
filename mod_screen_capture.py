"""
mod_screen_capture.py

Launches the native C++ capture engine (cpp/roomlights_capture.exe) as a subprocess.
The C++ binary does the real work identically to Prismatik's DDuplGrabber:
  - DXGI Desktop Duplication, AcquireNextFrame(timeout=0) (non-blocking)
  - GPU mip-chain /8 downscale before CPU readback
  - Direct UDP DNRGB to WLED

Falls back to a pure-Python implementation using dxcam / mss if the binary
is not compiled yet.

Build the binary:
  cd cpp
  cmake -B build -G "Visual Studio 17 2022" -A x64
  cmake --build build --config Release
  copy build\\Release\\RoomLightsCapture.exe roomlights_capture.exe
"""

import asyncio
import ctypes
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

import config
from state import state, Seg0Source

log = logging.getLogger("screen_capture")
RGB = Tuple[int, int, int]

_UDP_PORT          = 21324
_REALTIME_TIMEOUT  = 5    # seconds — WLED realtime hold timeout
_BINARY_NAME       = "roomlights_capture.exe"

# Enable Windows 1ms scheduler precision
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
# Prismatik profile loader (used by Python fallback)
# ---------------------------------------------------------------------------
def _load_prismatik_profile() -> Optional[List[Tuple[int, int, int, int]]]:
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
                log.info("Loaded %d LED zones from Prismatik: %s",
                         len(zones), ini_path.name)
                return zones
        except Exception as exc:
            log.warning("Could not parse %s: %s", ini_path, exc)
    return None


def _fallback_zones(h: int, w: int) -> List[Tuple[int, int, int, int]]:
    mid = w // 2
    zones = []
    for i in range(18):
        zones.append((int(h*.88), int(h*.98), mid+(w-mid)*i//18, mid+(w-mid)*(i+1)//18))
    for i in range(18):
        zones.append((h-h*(i+1)//18, h-h*i//18, int(w*.88), int(w*.98)))
    for i in range(36):
        zones.append((int(h*.02), int(h*.12), w-w*(i+1)//36, w-w*i//36))
    for i in range(18):
        zones.append((h*i//18, h*(i+1)//18, int(w*.02), int(w*.12)))
    for i in range(19):
        zones.append((int(h*.88), int(h*.98), mid*i//19, mid*(i+1)//19))
    return zones


# ---------------------------------------------------------------------------
# C++ subprocess launcher
# ---------------------------------------------------------------------------
def _find_binary() -> Optional[Path]:
    """Look for the pre-built roomlights_capture.exe."""
    candidates = [
        Path(__file__).parent / _BINARY_NAME,
        Path(__file__).parent / "cpp" / _BINARY_NAME,
        Path(__file__).parent / "cpp" / "build" / "Release" / "RoomLightsCapture.exe",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


async def _run_cpp_binary(binary: Path) -> bool:
    """
    Launch the C++ capture binary with dynamic segment bounds pulled from WLED.
    It streams UDP packets directly to WLED at 60 FPS.
    """
    from wled_api import WLEDClient

    # 1. Discover live segment bounds from WLED over HTTP (or use already-discovered cache)
    seg_info = dict(state.wled_segments)
    if not seg_info:
        try:
            async with WLEDClient() as wled:
                seg_info = await wled.fetch_segment_info(timeout_sec=5.0)
                if seg_info:
                    state.set_discovered_segments(seg_info)
        except Exception as exc:
            log.warning("Could not query WLED segments (%s). Using defaults.", exc)

    # Calculate total physical strip length
    total_leds = 150
    if seg_info:
        total_leds = max((s.get("stop", 0) for s in seg_info.values()), default=150)
        if total_leds <= 0:
            total_leds = 150

    # Resolve Seg 0 (Screen Ambient)
    s0 = seg_info.get(config.SEG0_ID, {})
    seg0_start = s0.get("start", config.SEG0_START_LED)
    seg0_count = s0.get("len", config.SEG0_COUNT)
    seg0_rev = 1 if (s0.get("rev", False) ^ config.INVERT_SCREEN_CAPTURE) else 0

    # Resolve Seg 1 (Left Lightbar)
    s1 = seg_info.get(config.SEG1_ID, {})
    seg1_start = s1.get("start", 0) if config.SEG1_ID >= 0 else -1
    seg1_count = s1.get("len", config.SEG1_COUNT)
    seg1_rev = 1 if (s1.get("rev", False) ^ config.INVERT_LIGHTBAR_LEFT) else 0

    # Resolve Seg 2 (Right Lightbar)
    s2 = seg_info.get(config.SEG2_ID, {})
    seg2_start = s2.get("start", 126) if config.SEG2_ID >= 0 else -1
    seg2_count = s2.get("len", config.SEG2_COUNT)
    seg2_rev = 1 if (s2.get("rev", False) ^ config.INVERT_LIGHTBAR_RIGHT) else 0

    # Resolve Single Lightbar (if configured)
    s_single = seg_info.get(config.SEG_LIGHTBAR_ID, {})
    single_start = s_single.get("start", -1) if config.SEG_LIGHTBAR_ID >= 0 else -1
    single_count = s_single.get("len", 0)
    single_rev = 1 if (s_single.get("rev", False) ^ config.INVERT_LIGHTBAR) else 0

    # Resolve Seg 3 (Pomodoro / Aux)
    s3 = seg_info.get(config.SEG3_ID, {})
    seg3_start = s3.get("start", 144) if config.SEG3_ID >= 0 else -1
    seg3_count = s3.get("len", config.SEG3_COUNT)
    seg3_rev = 1 if (s3.get("rev", False) ^ config.INVERT_POMODORO) else 0

    cmd = [
        str(binary),
        config.WLED_IP,
        str(config.SCREEN_CAPTURE_FPS),
        config.PRISMATIK_PROFILE,
        str(total_leds),
        str(seg0_start),
        str(seg0_count),
        str(seg0_rev),
        str(seg1_start),
        str(seg1_count),
        str(seg1_rev),
        str(seg2_start),
        str(seg2_count),
        str(seg2_rev),
        str(single_start),
        str(single_count),
        str(single_rev),
        str(seg3_start),
        str(seg3_count),
        str(seg3_rev),
    ]
    log.info("Launching Universal C++ Engine with dynamic WLED layout: %s", " ".join(cmd))

    consecutive_failures = 0
    while not state.shutdown_event.is_set():
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except Exception as exc:
            log.error("Failed to start C++ capture binary: %s", exc)
            return False

        # Stream output from the subprocess
        async def _drain_output():
            if proc.stdout:
                async for line in proc.stdout:
                    log.info("[capture] %s", line.decode(errors="ignore").rstrip())

        drain_task = asyncio.create_task(_drain_output())

        # Wait for process to exit OR shutdown
        done, _ = await asyncio.wait(
            [asyncio.create_task(proc.wait()),
             asyncio.create_task(state.shutdown_event.wait())],
            return_when=asyncio.FIRST_COMPLETED,
        )

        if state.shutdown_event.is_set():
            proc.terminate()
            await proc.wait()
            drain_task.cancel()
            return True

        ret = proc.returncode
        drain_task.cancel()
        consecutive_failures += 1

        if consecutive_failures >= 3:
            log.error("C++ capture binary crashed %d times. Falling back to Python.",
                      consecutive_failures)
            return False

        log.warning("C++ capture binary exited (code %d). Restarting in 2s...", ret)
        await asyncio.sleep(2.0)

    return True


# ---------------------------------------------------------------------------
# Python fallback capture engine
# ---------------------------------------------------------------------------
class PythonCaptureEngine:
    """Pure-Python screen capture — used only when C++ binary is not available."""

    def __init__(self) -> None:
        self._camera = None
        self._sct = None
        self._monitor = None
        self._width = 0
        self._height = 0
        self._zones: List[Tuple[int, int, int, int]] = []
        self._sock: Optional[socket.socket] = None
        self._running = False
        self._header = bytes([0x04, _REALTIME_TIMEOUT, (17 >> 8) & 0xFF, 17 & 0xFF])
        self._keepalive_pkt = self._header + bytes(config.SEG0_COUNT * 3)

    def start(self) -> bool:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)

        if _USE_DXCAM:
            try:
                self._camera = dxcam.create(output_color="BGR", max_buffer_len=1)
                self._camera.start(target_fps=config.SCREEN_CAPTURE_FPS, video_mode=False)
                for _ in range(60):
                    frame = self._camera.get_latest_frame()
                    if frame is not None:
                        self._height, self._width = frame.shape[:2]
                        break
                    time.sleep(0.016)
                else:
                    self._height, self._width = 1080, 1920
                log.info("Python fallback: dxcam @ %d FPS: %dx%d",
                         config.SCREEN_CAPTURE_FPS, self._width, self._height)
            except Exception as exc:
                log.warning("dxcam failed (%s). Trying mss.", exc)
                return self._start_mss()
        else:
            return self._start_mss()

        self._init_zones()
        return True

    def _start_mss(self) -> bool:
        try:
            import mss as mss_mod
            self._sct = mss_mod.mss()
            mon = self._sct.monitors[1]
            self._width, self._height = mon["width"], mon["height"]
            self._monitor = mon
            self._init_zones()
            log.info("Python fallback: mss %dx%d", self._width, self._height)
            return True
        except Exception as exc:
            log.error("mss failed: %s", exc)
            return False

    def _init_zones(self) -> None:
        loaded = _load_prismatik_profile()
        if loaded and len(loaded) == config.SEG0_COUNT:
            self._zones = loaded
        else:
            self._zones = _fallback_zones(self._height, self._width)

    def stop(self) -> None:
        self._running = False
        if self._camera:
            try: self._camera.stop()
            except Exception: pass
            self._camera = None
        if self._sct:
            try: self._sct.close()
            except Exception: pass
            self._sct = None
        if self._sock:
            self._sock.close()
            self._sock = None

    def _grab(self) -> Optional[np.ndarray]:
        if self._camera:
            return self._camera.get_latest_frame()
        if self._sct:
            try:
                raw = self._sct.grab(self._monitor)
                return np.array(raw, dtype=np.uint8)[:, :, :3]
            except Exception:
                return None
        return None

    def _compute(self, frame: np.ndarray) -> np.ndarray:
        n = len(self._zones)
        h, w = frame.shape[:2]
        raw = np.empty((n, 3), dtype=np.float32)
        for idx, (y1, y2, x1, x2) in enumerate(self._zones):
            r1 = max(0, min(h, y1)); r2 = max(r1+1, min(h, y2))
            c1 = max(0, min(w, x1)); c2 = max(c1+1, min(w, x2))
            region = frame[r1:r2, c1:c2]
            raw[idx] = region.mean(axis=(0, 1)) if region.size > 0 else 0.0

        rgb = raw[:, [2, 1, 0]] / 255.0
        max_c = rgb.max(axis=1, keepdims=True)
        rgb = np.clip(max_c - (max_c - rgb) * config.SCREEN_CAPTURE_SATURATION, 0.0, 1.0)
        rgb = np.power(rgb, config.SCREEN_CAPTURE_GAMMA)
        return (rgb * 255.0).astype(np.uint8)

    def _send(self, colors_u8: np.ndarray) -> None:
        if not self._sock: return
        try:
            self._sock.sendto(self._header + colors_u8.tobytes(),
                              (config.WLED_IP, _UDP_PORT))
        except (BlockingIOError, OSError):
            pass

    def run_loop(self) -> None:
        interval = 1.0 / config.SCREEN_CAPTURE_FPS
        keepalive_every = _REALTIME_TIMEOUT / 2.0
        last_ka = 0.0
        self._running = True
        log.info("Python fallback capture loop @ %d FPS.", config.SCREEN_CAPTURE_FPS)

        while self._running:
            t0 = time.monotonic()

            if state.seg0_source == Seg0Source.SCREEN_CAPTURE:
                frame = self._grab()
                if frame is not None:
                    state.latest_frame = frame
                    colors_u8 = self._compute(frame)
                    state.update_seg0_colors([tuple(map(int, r)) for r in colors_u8])
                    self._send(colors_u8)
                    last_ka = t0
                elif t0 - last_ka >= keepalive_every:
                    self._sock and self._sock.sendto(self._keepalive_pkt,
                                                     (config.WLED_IP, _UDP_PORT))
                    last_ka = t0
            elif t0 - last_ka >= keepalive_every:
                self._sock and self._sock.sendto(self._keepalive_pkt,
                                                 (config.WLED_IP, _UDP_PORT))
                last_ka = t0

            elapsed = time.monotonic() - t0
            wait = interval - elapsed
            if wait > 0.001:
                time.sleep(wait)


# ---------------------------------------------------------------------------
# Module entry point
# ---------------------------------------------------------------------------
async def run() -> None:
    binary = _find_binary()

    if binary:
        log.info("C++ capture engine found: %s", binary)
        log.info("Using Prismatik-identical DDupl + GPU mip /8 architecture.")
        ok = await _run_cpp_binary(binary)
        if ok:
            return
        log.warning("C++ binary failed — falling back to Python capture.")
    else:
        log.warning("C++ capture binary not found at %s.", _BINARY_NAME)
        log.warning("For lowest latency, build it:")
        log.warning("  cd cpp && cmake -B build -G \"Visual Studio 17 2022\" -A x64")
        log.warning("  cmake --build build --config Release")
        log.warning("  copy build\\Release\\RoomLightsCapture.exe ..")
        log.warning("Falling back to Python (dxcam/mss) — expect ~100-200ms latency.")

    # Python fallback
    engine = PythonCaptureEngine()
    if not await asyncio.to_thread(engine.start):
        log.error("Python capture fallback also failed.")
        return

    thread = threading.Thread(target=engine.run_loop,
                              name="py_capture", daemon=True)
    thread.start()

    await state.shutdown_event.wait()
    engine.stop()
    thread.join(timeout=2.0)
    log.info("Screen capture stopped.")
