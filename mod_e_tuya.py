"""
mod_e_tuya.py — Ambient ceiling light driven by live screen color.

Samples the average color of your screen every 0.5 seconds and smoothly
crossfades the Tuya ceiling bulb to match, creating a cinematic ambient
glow that works universally for games, movies, and desktop use.

Features:
  - Dark pixel filtering: ignores pixels below brightness threshold so
    dark scenes don't wash the room in muddy grey or turn the light off.
  - Smooth 2-second ease-in-out crossfade between color updates.
  - Hotkey toggle (Ctrl+Shift+L): instantly switch between RoomLights
    control and manual/uncontrolled (bulb untouched).
  - Uses the same dxcam/mss backend as mod_screen_capture.py.

Prerequisite: Tuya credentials in .env (TUYA_DEVICE_ID, TUYA_LOCAL_KEY, TUYA_IP).
Get them with: python -m tinytuya wizard
"""

import asyncio
import logging
import time
from typing import Optional, Tuple

import numpy as np

try:
    import tinytuya
    TINYTUYA_AVAILABLE = True
except ImportError:
    TINYTUYA_AVAILABLE = False

try:
    from pynput import keyboard as pynput_keyboard
    PYNPUT_AVAILABLE = True
except ImportError:
    PYNPUT_AVAILABLE = False

# Screen capture backend (reuse from mod_screen_capture logic)
try:
    import dxcam
    _USE_DXCAM = True
except ImportError:
    _USE_DXCAM = False
    try:
        import mss as mss_mod
    except ImportError:
        pass

import config
from state import state

log = logging.getLogger("tuya")

RGB = Tuple[int, int, int]

# -----------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------
_SAMPLE_INTERVAL = 0.5          # seconds between screen samples
_CROSSFADE_DURATION = 2.0       # seconds to fade to new color
_CROSSFADE_STEPS = 20           # interpolation steps
_DARK_THRESHOLD = 40            # pixel brightness (0-255) below which to ignore
_MIN_BRIGHTNESS_PCT = 20        # minimum bulb brightness % (prevent turning off)
_MAX_BRIGHTNESS_PCT = 90        # maximum bulb brightness %

# Neutral warm white used when ambient is disabled or no screen data
_NEUTRAL_RGB: RGB = (255, 200, 120)
_NEUTRAL_BRI: int = 60


# -----------------------------------------------------------------------
# Hotkey listener (runs in background thread via pynput)
# -----------------------------------------------------------------------
def _start_hotkey_listener() -> None:
    """
    Hotkeys (configured via .env / config.py):
      KEYBIND_TUYA_TOGGLE          : toggle ambient on/off
      KEYBIND_TUYA_BRIGHTNESS_UP    : brightness +1% (max 100%)
      KEYBIND_TUYA_BRIGHTNESS_DOWN  : brightness -1% (min 1%)
    """
    if not PYNPUT_AVAILABLE:
        log.warning("pynput not installed — hotkeys unavailable.")
        return

    def on_toggle():
        state.tuya_ambient_enabled = not state.tuya_ambient_enabled
        status = "ON" if state.tuya_ambient_enabled else "OFF"
        log.info("Tuya ambient toggled: %s  (brightness %d%%)",
                 status, state.tuya_brightness)

    def on_brightness_up():
        state.tuya_brightness = min(100, state.tuya_brightness + 1)
        log.info("Tuya brightness: %d%%", state.tuya_brightness)

    def on_brightness_down():
        state.tuya_brightness = max(1, state.tuya_brightness - 1)
        log.info("Tuya brightness: %d%%", state.tuya_brightness)

    hotkey_dict = {
        config.KEYBIND_TUYA_TOGGLE:          on_toggle,
        config.KEYBIND_TUYA_BRIGHTNESS_UP:    on_brightness_up,
        config.KEYBIND_TUYA_BRIGHTNESS_DOWN:  on_brightness_down,
    }

    hotkeys = pynput_keyboard.GlobalHotKeys(hotkey_dict)
    hotkeys.start()
    log.info(
        "Tuya hotkeys registered: toggle='%s' | up='%s' | down='%s' (current: %d%%)",
        config.KEYBIND_TUYA_TOGGLE,
        config.KEYBIND_TUYA_BRIGHTNESS_UP,
        config.KEYBIND_TUYA_BRIGHTNESS_DOWN,
        state.tuya_brightness,
    )



# -----------------------------------------------------------------------
# Screen color sampler
# -----------------------------------------------------------------------
class ScreenColorSampler:
    """
    Grabs a downscaled screen frame and returns the average color of
    all non-dark pixels.
    """

    def __init__(self) -> None:
        self._camera = None
        self._sct = None
        self._monitor = None

    def start(self) -> bool:
        if _USE_DXCAM:
            try:
                self._camera = dxcam.create(output_color="BGR")
                log.info("Tuya sampler using dxcam.")
                return True
            except Exception as exc:
                log.warning("dxcam failed for Tuya sampler: %s. Trying mss.", exc)

        try:
            self._sct = mss_mod.mss()
            self._monitor = self._sct.monitors[1]
            log.info("Tuya sampler using mss.")
            return True
        except Exception as exc:
            log.error("Screen sampler init failed: %s", exc)
            return False

    def stop(self) -> None:
        self._camera = None
        self._sct = None

    def sample(self) -> Optional[RGB]:
        """
        Capture frame, filter dark pixels, return average RGB.
        Returns None if no bright pixels found or capture fails.
        """
        frame = self._grab()
        if frame is None:
            return None

        # Downscale to tiny size for fast processing (30x17 ≈ 510 pixels)
        try:
            # Simple block downscale using numpy reshape + mean
            h, w = frame.shape[:2]
            # Resize to ~320x180 using slicing
            small = frame[::h // 18, ::w // 32, :3]  # roughly 18x32 samples
        except Exception:
            small = frame[:, :, :3]

        # Flatten to (N, 3) — BGR
        pixels = small.reshape(-1, 3).astype(np.float32)

        # Compute per-pixel brightness (simple average of channels)
        brightness = pixels.mean(axis=1)

        # Filter out dark pixels
        bright_mask = brightness > _DARK_THRESHOLD
        bright_pixels = pixels[bright_mask]

        if len(bright_pixels) < 10:
            # Not enough bright pixels — screen is mostly dark
            return None

        # Average remaining pixels
        avg = bright_pixels.mean(axis=0)  # BGR
        r, g, b = int(avg[2]), int(avg[1]), int(avg[0])  # BGR -> RGB

        return (r, g, b)

    def _grab(self) -> Optional[np.ndarray]:
        if _USE_DXCAM and self._camera:
            try:
                return self._camera.grab()
            except Exception:
                return None
        elif self._sct:
            try:
                raw = self._sct.grab(self._monitor)
                return np.array(raw, dtype=np.uint8)
            except Exception:
                return None
        return None


# -----------------------------------------------------------------------
# Tuya bulb controller
# -----------------------------------------------------------------------
def _ease_in_out(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)


def _lerp_rgb(a: RGB, b: RGB, t: float) -> RGB:
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )


def _rgb_to_brightness(rgb: RGB) -> int:
    """
    Map the average brightness of an RGB color to a bulb brightness %.
    Clamps between MIN and MAX to keep room usable.
    """
    avg = (rgb[0] + rgb[1] + rgb[2]) / 3.0 / 255.0
    bri = _MIN_BRIGHTNESS_PCT + avg * (_MAX_BRIGHTNESS_PCT - _MIN_BRIGHTNESS_PCT)
    return int(max(_MIN_BRIGHTNESS_PCT, min(_MAX_BRIGHTNESS_PCT, bri)))


class TuyaBulbController:
    """Controls the Tuya ceiling bulb with smooth crossfades."""

    def __init__(self) -> None:
        self._device = None
        self._current_rgb: RGB = _NEUTRAL_RGB
        self._current_bri: int = _NEUTRAL_BRI
        self._connected = False
        self._transition_task: Optional[asyncio.Task] = None

    def connect(self) -> bool:
        if not TINYTUYA_AVAILABLE:
            log.error("tinytuya not installed. Run: pip install tinytuya")
            return False
        if not all([config.TUYA_DEVICE_ID, config.TUYA_LOCAL_KEY, config.TUYA_IP]):
            log.error("Tuya credentials missing in .env.")
            return False
        try:
            self._device = tinytuya.BulbDevice(
                dev_id=config.TUYA_DEVICE_ID,
                address=config.TUYA_IP,
                local_key=config.TUYA_LOCAL_KEY,
            )
            self._device.set_version(config.TUYA_VERSION)
            self._device.set_socketPersistent(True)
            self._connected = True
            log.info("Tuya bulb connected at %s.", config.TUYA_IP)
            return True
        except Exception as exc:
            log.error("Tuya connection failed: %s", exc)
            return False

    def _sync_set_brightness_instant(self, brightness_pct: int) -> None:
        """Blocking: set brightness instantly with no fade."""
        if not self._device:
            return
        try:
            bri = max(1, min(100, brightness_pct))
            self._device.set_brightness_percentage(bri, nowait=True)
            self._current_bri = bri
        except Exception as exc:
            log.debug("Tuya brightness instant error: %s", exc)
            self._connected = False

    async def set_brightness_instant(self, brightness_pct: int) -> None:
        """Set brightness instantly (no crossfade). Cancels any running fade."""
        if self._transition_task and not self._transition_task.done():
            self._transition_task.cancel()
            try:
                await self._transition_task
            except asyncio.CancelledError:
                pass
        await asyncio.to_thread(self._sync_set_brightness_instant, brightness_pct)

    def _sync_set_color(self, rgb: RGB, brightness_pct: int) -> None:
        """Blocking: send color + brightness to the bulb."""
        if not self._device:
            return
        try:
            bri = max(0, min(100, brightness_pct))
            self._device.set_brightness_percentage(bri, nowait=True)
            self._device.set_colour(rgb[0], rgb[1], rgb[2], nowait=True)
        except Exception as exc:
            log.debug("Tuya send error: %s", exc)
            self._connected = False

    async def transition_to(
        self,
        target_rgb: RGB,
        target_bri: int,
        duration: float = _CROSSFADE_DURATION,
    ) -> None:
        """Smooth crossfade to target_rgb over duration seconds."""
        # Cancel any in-progress fade
        if self._transition_task and not self._transition_task.done():
            self._transition_task.cancel()
            try:
                await self._transition_task
            except asyncio.CancelledError:
                pass

        start_rgb = self._current_rgb
        start_bri = self._current_bri
        delay = duration / _CROSSFADE_STEPS

        async def _worker() -> None:
            for i in range(1, _CROSSFADE_STEPS + 1):
                t = _ease_in_out(i / _CROSSFADE_STEPS)
                curr_rgb = _lerp_rgb(start_rgb, target_rgb, t)
                curr_bri = int(start_bri + (target_bri - start_bri) * t)
                await asyncio.to_thread(self._sync_set_color, curr_rgb, curr_bri)
                await asyncio.sleep(delay)

            self._current_rgb = target_rgb
            self._current_bri = target_bri

        self._transition_task = asyncio.create_task(_worker())
        try:
            await self._transition_task
        except asyncio.CancelledError:
            pass


# -----------------------------------------------------------------------
# Module entry point
# -----------------------------------------------------------------------
async def run() -> None:
    """
    Main Tuya ambient task:
      - Samples screen color every 0.5s
      - Crossfades bulb to match over 2s
      - Responds to Ctrl+Shift+L toggle
    """
    if not TINYTUYA_AVAILABLE:
        log.error("tinytuya not available — Tuya ambient disabled.")
        return

    # Start hotkey listener in background
    _start_hotkey_listener()

    # Init screen sampler
    sampler = ScreenColorSampler()
    sampler_ok = await asyncio.to_thread(sampler.start)
    if not sampler_ok:
        log.warning("Screen sampler unavailable — using neutral color only.")

    # Connect to Tuya bulb (retry until success)
    ctrl = TuyaBulbController()
    connected = False
    while not state.shutdown_event.is_set() and not connected:
        connected = await asyncio.to_thread(ctrl.connect)
        if not connected:
            log.warning("Tuya: retrying in 10s...")
            await asyncio.sleep(10.0)

    if not connected:
        return

    # Start at neutral
    await ctrl.transition_to(_NEUTRAL_RGB, _NEUTRAL_BRI)

    last_color: RGB = _NEUTRAL_RGB
    last_enabled: bool = True
    log.info("Tuya ambient running. Toggle key: '%s'", config.KEYBIND_TUYA_TOGGLE)

    while not state.shutdown_event.is_set():
        await asyncio.sleep(_SAMPLE_INTERVAL)

        enabled = state.tuya_ambient_enabled

        # Toggled OFF: fade back to neutral and leave it there
        if not enabled:
            if last_enabled:
                log.info("Tuya ambient disabled — holding neutral.")
                await ctrl.transition_to(_NEUTRAL_RGB, _NEUTRAL_BRI)
            last_enabled = False
            continue

        last_enabled = True

        # Toggled back ON: immediately sample and fade
        if not sampler_ok:
            continue

        sampled = await asyncio.to_thread(sampler.sample)

        if sampled is None:
            # Screen is very dark — dim to minimum but keep current hue
            target = last_color
            target_bri = 1
        else:
            target = sampled
            target_bri = state.tuya_brightness  # Always use manual brightness

        # Only update if color changed meaningfully OR brightness changed
        bri_delta = abs(target_bri - ctrl._current_bri)
        color_delta = sum(abs(target[i] - last_color[i]) for i in range(3))

        if color_delta > 15:
            # Color changed — smooth crossfade
            asyncio.create_task(ctrl.transition_to(target, target_bri))
            last_color = target
        elif bri_delta >= 1:
            # Brightness-only change — instant, no fade
            asyncio.create_task(ctrl.set_brightness_instant(target_bri))

        # Reconnect if dropped
        if not ctrl._connected:
            log.warning("Tuya: lost connection, reconnecting...")
            await asyncio.to_thread(ctrl.connect)

    await asyncio.to_thread(sampler.stop)
    log.info("Tuya ambient stopped.")
