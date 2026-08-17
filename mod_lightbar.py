"""
mod_lightbar.py — Unified Seg 1 + Seg 2 lightbar state machine.

This is the central rendering loop for the 35-LED logical bar.
It reads state.lightbar_mode and dispatches to the appropriate renderer.

Priority order (highest first):
  CS2_FLASH  -> 2s all-white, then restore (flashbang)
  CS2_PULSE  -> red breathing pulse (low health < 20)
  DS4_ACTIVE -> solid color from DS4 lightbar callback
  REV_METER  -> green/yellow/red/blue fill from telemetry (fallback)
  IDLE       -> both segments off

Rev meter zone layout (35-LED logical bar, pos 0=far left, 34=far right):
  Green:  pos 0-6   + pos 28-34  (7 LEDs each outer edge)
  Yellow: pos 7-15  + pos 19-27  (9 LEDs each inner)
  Red:    pos 16-18              (3 LEDs, center)

Fill direction: from outer edges inward as RPM climbs.
At >= REV_LIMITER_PCT: entire bar flashes blue at REV_FLASH_HZ.
"""

import asyncio
import logging
import math
import time
from typing import List, Tuple

import config
from state import state, LightbarMode
from wled_api import WLEDClient

log = logging.getLogger("lightbar")

RGB = Tuple[int, int, int]

# Rev meter colors
_GREEN  = (0, 255, 0)
_YELLOW = (255, 165, 0)
_RED    = (255, 0, 0)
_BLUE   = (0, 100, 255)
_WHITE  = (255, 255, 255)
_OFF    = (0, 0, 0)

# Rev meter zone color assignment per logical position (0-34)
# Pre-computed once at import time.
_ZONE_COLORS: List[RGB] = []
for _p in range(config.LIGHTBAR_TOTAL):
    if _p in config.REV_GREEN_ZONE:
        _ZONE_COLORS.append(_GREEN)
    elif _p in config.REV_YELLOW_ZONE:
        _ZONE_COLORS.append(_YELLOW)
    else:
        _ZONE_COLORS.append(_RED)


def _build_rev_meter_array(rpm_pct: float, flash_blue: bool) -> List[RGB]:
    """
    Build a 35-element color array representing the current RPM state.

    Args:
        rpm_pct:    Engine RPM as 0.0-1.0 fraction of max RPM.
        flash_blue: If True, the entire bar is blue (limiter flash).
    """
    if flash_blue:
        return [_BLUE] * config.LIGHTBAR_TOTAL

    colors = [_OFF] * config.LIGHTBAR_TOTAL

    if rpm_pct < config.REV_START_PCT:
        return colors  # All dark below power band

    # Determine how many LEDs to light on each side.
    # Fill goes from outer edges inward:
    #   Outer positions: pos 0 (left) and pos 34 (right) are outer tips.
    #   Inner positions: pos 17 (left inner) and pos 18 (right inner).
    #
    # Map rpm_pct to number of LEDs lit on each half:
    #   At REV_START_PCT: 0 LEDs
    #   At REV_GREEN_PCT: 7 LEDs (outer green tips fully lit)
    #   At REV_YELLOW_PCT: 7+9=16 LEDs (green+yellow, center not yet)
    #   At REV_FULL_PCT+: 17/18 LEDs (all, strip fully lit)

    half = config.LIGHTBAR_TOTAL // 2  # 17 (left half Seg2 = 18, right half Seg1 = 17)

    # Normalise rpm into 0..1 range between START and FULL
    span = config.REV_FULL_PCT - config.REV_START_PCT
    if span <= 0:
        return colors
    norm = (rpm_pct - config.REV_START_PCT) / span
    norm = max(0.0, min(1.0, norm))

    # Number of LEDs to light per side (left half: 0-17, right half: 18-34)
    left_count  = round(norm * (config.SEG2_COUNT))   # 0-18
    right_count = round(norm * (config.SEG1_COUNT))    # 0-17

    # Left side: fill from pos 0 rightward
    for i in range(min(left_count, config.SEG2_COUNT)):
        colors[i] = _ZONE_COLORS[i]

    # Right side: fill from pos 34 leftward
    for i in range(min(right_count, config.SEG1_COUNT)):
        pos = config.LIGHTBAR_TOTAL - 1 - i  # 34, 33, 32 ...
        colors[pos] = _ZONE_COLORS[pos]

    return colors


def _ease_in_out(t: float) -> float:
    """Smooth ease-in-out interpolation for brightness pulse."""
    return t * t * (3 - 2 * t)


class LightbarRenderer:
    """
    Drives the unified 35-LED lightbar based on the current mode in state.

    Runs as an asyncio task at up to LIGHTBAR_UPDATE_HZ Hz.
    """

    def __init__(self, wled: WLEDClient) -> None:
        self._wled = wled
        self._flash_phase = False       # Blue flash toggle
        self._last_flash_toggle = 0.0
        self._flash_interval = 1.0 / config.REV_FLASH_HZ

        # Pulse state for CS2_PULSE (red breathing)
        self._pulse_start = 0.0
        self._pulse_period = 1.5        # seconds for one breath cycle

    async def run(self) -> None:
        """Main rendering loop — runs until shutdown."""
        min_interval = 1.0 / config.LIGHTBAR_UPDATE_HZ
        log.info("Lightbar renderer started.")

        while not state.shutdown_event.is_set():
            loop_start = time.monotonic()

            await self._render_frame()

            elapsed = time.monotonic() - loop_start
            sleep_for = max(0.0, min_interval - elapsed)
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)

        # Shutdown — turn off both segments
        await self._wled.set_lightbar_off()
        log.info("Lightbar renderer stopped.")

    async def _render_frame(self) -> None:
        """Render one frame based on the current lightbar mode."""
        mode = state.lightbar_mode

        if mode == LightbarMode.CS2_FLASH:
            await self._render_cs2_flash()

        elif mode == LightbarMode.CS2_PULSE:
            await self._render_cs2_pulse()

        elif mode == LightbarMode.DS4_ACTIVE:
            await self._render_ds4()

        elif mode == LightbarMode.REV_METER:
            await self._render_rev_meter()

        else:  # IDLE
            await self._render_idle()

        # Auto-detect mode transitions
        await self._update_mode()

    async def _update_mode(self) -> None:
        """
        Automatically switch lightbar mode based on state signals.

        Priority: CS2_FLASH > CS2_PULSE > DS4_ACTIVE > REV_METER > IDLE
        CS2 flash/pulse modes are set externally by mod_b_cs2_gsi.py.
        """
        mode = state.lightbar_mode

        # Don't override CS2 event modes here — they restore themselves
        if mode in (LightbarMode.CS2_FLASH, LightbarMode.CS2_PULSE):
            return

        ds4_on = state.is_ds4_active(config.DS4_LIGHTBAR_TIMEOUT)
        racing = state.rpm_pct > config.REV_START_PCT

        if ds4_on:
            if mode != LightbarMode.DS4_ACTIVE:
                await state.set_mode(LightbarMode.DS4_ACTIVE)
        elif racing:
            if mode != LightbarMode.REV_METER:
                await state.set_mode(LightbarMode.REV_METER)
        else:
            if mode not in (LightbarMode.IDLE,):
                await state.set_mode(LightbarMode.IDLE)

    # ------------------------------------------------------------------
    # Renderers
    # ------------------------------------------------------------------

    async def _render_cs2_flash(self) -> None:
        """All-white flash — just hold white; restore is handled by gsi module."""
        await self._wled.set_lightbar_solid(_WHITE)

    async def _render_cs2_pulse(self) -> None:
        """Red breathing pulse for low health."""
        if self._pulse_start == 0.0:
            self._pulse_start = time.monotonic()

        elapsed = time.monotonic() - self._pulse_start
        # Sine wave 0..1..0 over _pulse_period seconds
        t = (elapsed % self._pulse_period) / self._pulse_period
        brightness_norm = _ease_in_out(abs(math.sin(math.pi * t)))
        brightness = int(brightness_norm * 255)
        r = brightness
        await self._wled.set_lightbar_solid((r, 0, 0))

    async def _render_ds4(self) -> None:
        """Solid color matching the DS4 lightbar RGB."""
        r, g, b = state.get_lightbar_rgb()
        await self._wled.set_lightbar_solid((r, g, b))

    async def _render_rev_meter(self) -> None:
        """Green/yellow/red fill from outer edges inward. Blue flash at limiter."""
        rpm = state.rpm_pct
        now = time.monotonic()

        if rpm >= config.REV_LIMITER_PCT:
            # Blue flash at REV_FLASH_HZ
            if now - self._last_flash_toggle >= self._flash_interval:
                self._flash_phase = not self._flash_phase
                self._last_flash_toggle = now
            color_array = _build_rev_meter_array(rpm, flash_blue=self._flash_phase)
        else:
            self._flash_phase = False
            color_array = _build_rev_meter_array(rpm, flash_blue=False)

        await self._wled.set_lightbar(color_array)

    async def _render_idle(self) -> None:
        """Both segments off in idle mode."""
        await self._wled.set_lightbar_off()


# ---------------------------------------------------------------------------
# Module entry point
# ---------------------------------------------------------------------------

async def run(wled: WLEDClient) -> None:
    """Start the lightbar renderer. Called from main.py."""
    renderer = LightbarRenderer(wled)
    await renderer.run()
