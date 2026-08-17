"""
mod_e_tuya.py — Ambient ceiling light control via local Tuya protocol.

Controls the Homemate (Tuya-based) ceiling bulb on the local network.
No cloud dependency — communicates directly over LAN via tinytuya.

Features:
  - Monitors state.context and crossfades to the appropriate color.
  - Smooth 20-step ease-in-out crossfade over 2 seconds.
  - Persistent TCP socket (set_socketPersistent) for low-latency commands.
  - Updates infrequently — only on context change, not every frame.

Context → color mapping is defined in config.TUYA_CONTEXT_MAP.

Prerequisite: obtain Tuya local key via:
  python -m tinytuya wizard
Then paste TUYA_DEVICE_ID, TUYA_LOCAL_KEY, TUYA_IP into .env.
"""

import asyncio
import logging
import time
from typing import Tuple

try:
    import tinytuya
    TINYTUYA_AVAILABLE = True
except ImportError:
    TINYTUYA_AVAILABLE = False

import config
from state import state, AppContext

log = logging.getLogger("tuya")

RGB = Tuple[int, int, int]


def _ease_in_out(t: float) -> float:
    """Smooth ease-in-out curve: t in [0,1] -> smoothed [0,1]."""
    return t * t * (3.0 - 2.0 * t)


def _lerp_rgb(a: RGB, b: RGB, t: float) -> RGB:
    """Linearly interpolate between two RGB colors."""
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )


class TuyaBulbController:
    """
    Controls the Tuya ceiling bulb with smooth color crossfades.

    Crossfades run in asyncio.to_thread() so they don't block the event loop.
    Only one crossfade runs at a time — starting a new one cancels the previous.
    """

    def __init__(self) -> None:
        self._device = None
        self._current_rgb: RGB = (255, 200, 120)
        self._current_bri: int = 60
        self._transition_task: asyncio.Task = None
        self._connected = False

    def _connect(self) -> bool:
        """Connect to the Tuya bulb. Returns True on success."""
        if not TINYTUYA_AVAILABLE:
            log.error("tinytuya not installed. Run: pip install tinytuya")
            return False

        if not all([config.TUYA_DEVICE_ID, config.TUYA_LOCAL_KEY, config.TUYA_IP]):
            log.error("Tuya credentials not set in .env "
                      "(TUYA_DEVICE_ID, TUYA_LOCAL_KEY, TUYA_IP).")
            return False

        try:
            self._device = tinytuya.BulbDevice(
                dev_id=config.TUYA_DEVICE_ID,
                address=config.TUYA_IP,
                local_key=config.TUYA_LOCAL_KEY,
            )
            self._device.set_version(config.TUYA_VERSION)
            self._device.set_socketPersistent(True)  # Keep TCP socket open
            self._connected = True
            log.info("Tuya bulb connected at %s.", config.TUYA_IP)
            return True
        except Exception as exc:
            log.error("Tuya connection failed: %s", exc)
            return False

    def _sync_set_color(self, rgb: RGB, brightness_pct: int) -> None:
        """
        Blocking: send color+brightness to the Tuya bulb.
        Called via asyncio.to_thread().
        """
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
        duration: float = config.TUYA_CROSSFADE_DURATION,
        steps: int = config.TUYA_CROSSFADE_STEPS,
    ) -> None:
        """
        Smoothly crossfade to target_rgb over ``duration`` seconds.

        Cancels any in-progress crossfade before starting.
        """
        # Cancel ongoing transition
        if self._transition_task and not self._transition_task.done():
            self._transition_task.cancel()
            try:
                await self._transition_task
            except asyncio.CancelledError:
                pass

        start_rgb = self._current_rgb
        start_bri = self._current_bri
        delay = duration / steps

        async def _worker() -> None:
            for i in range(1, steps + 1):
                t = _ease_in_out(i / steps)
                curr_rgb = _lerp_rgb(start_rgb, target_rgb, t)
                curr_bri = int(start_bri + (target_bri - start_bri) * t)

                await asyncio.to_thread(self._sync_set_color, curr_rgb, curr_bri)
                await asyncio.sleep(delay)

            # Store final state
            self._current_rgb = target_rgb
            self._current_bri = target_bri

        self._transition_task = asyncio.create_task(_worker())
        try:
            await self._transition_task
        except asyncio.CancelledError:
            pass

    async def apply_context(self, ctx: AppContext) -> None:
        """Look up the color for ``ctx`` in config and crossfade to it."""
        ctx_name = ctx.name.lower()
        cfg = config.TUYA_CONTEXT_MAP.get(ctx_name)
        if cfg is None:
            log.warning("No Tuya color defined for context '%s'.", ctx_name)
            return

        target_rgb: RGB = cfg["rgb"]
        target_bri: int = cfg["brightness"]
        log.info("Tuya: context=%s -> rgb=%s bri=%d%%",
                 ctx_name, target_rgb, target_bri)
        await self.transition_to(target_rgb, target_bri)


async def run() -> None:
    """
    Main Tuya task: watch for context changes and crossfade accordingly.
    """
    if not TINYTUYA_AVAILABLE:
        log.error("tinytuya not available — Tuya ambient disabled.")
        return

    ctrl = TuyaBulbController()

    # Try to connect (retry on failure)
    connected = False
    while not state.shutdown_event.is_set() and not connected:
        connected = await asyncio.to_thread(ctrl._connect)
        if not connected:
            log.warning("Tuya: retrying connection in 10s...")
            await asyncio.sleep(10.0)

    if not connected:
        log.error("Tuya: giving up — check credentials in .env.")
        return

    # Apply initial idle color
    await ctrl.apply_context(AppContext.IDLE)

    last_context = state.context

    log.info("Tuya ambient monitor running.")

    while not state.shutdown_event.is_set():
        await asyncio.sleep(0.5)  # Poll context every 500ms

        current_context = state.context
        if current_context != last_context:
            last_context = current_context
            await ctrl.apply_context(current_context)

        # Reconnect if connection dropped
        if not ctrl._connected:
            log.warning("Tuya: lost connection, reconnecting...")
            await asyncio.to_thread(ctrl._connect)

    log.info("Tuya ambient stopped.")
