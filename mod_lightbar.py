"""
mod_lightbar.py - Unified Seg 1 + Seg 2 lightbar state machine.

Drives the 36-LED unified lightbar (Seg 1 left: 0..17, Seg 2 right: 18..35)
exclusively via HTTP JSON API (/json/state) to prevent UDP packet collisions
with Seg 0 (which streams realtime screen capture on UDP port 21324).

Priority order (highest first):
  CS2_FLASH  -> 2s all-white, then restore (flashbang)
  CS2_PULSE  -> red breathing pulse (low health < 20)
  DS4_ACTIVE -> solid color from DS4 lightbar callback
  REV_METER  -> green/yellow/red/blue fill from telemetry (fallback)
  IDLE       -> both segments off
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

_GREEN  = (0, 255, 0)
_YELLOW = (255, 165, 0)
_RED    = (255, 0, 0)
_BLUE   = (0, 100, 255)
_WHITE  = (255, 255, 255)
_OFF    = (0, 0, 0)

# Pre-compute rev meter zone colors for 36-LED logical bar
_ZONE_COLORS: List[RGB] = []
for _p in range(config.LIGHTBAR_TOTAL):
    if _p in config.REV_GREEN_ZONE:
        _ZONE_COLORS.append(_GREEN)
    elif _p in config.REV_YELLOW_ZONE:
        _ZONE_COLORS.append(_YELLOW)
    else:
        _ZONE_COLORS.append(_RED)


def _build_rev_meter_array(rpm_pct: float, flash_blue: bool) -> List[RGB]:
    if flash_blue:
        return [_BLUE] * config.LIGHTBAR_TOTAL

    colors = [_OFF] * config.LIGHTBAR_TOTAL
    if rpm_pct < config.REV_START_PCT:
        return colors

    span = config.REV_FULL_PCT - config.REV_START_PCT
    if span <= 0:
        return colors
    norm = max(0.0, min(1.0, (rpm_pct - config.REV_START_PCT) / span))

    # Fill from outer edges inward (0 -> 17 left, 35 -> 18 right)
    half = config.LIGHTBAR_TOTAL // 2  # 18 LEDs per side
    lit_per_side = round(norm * half)

    # Left side: fill from index 0 rightward
    for i in range(min(lit_per_side, config.SEG1_COUNT)):
        colors[i] = _ZONE_COLORS[i]

    # Right side: fill from index 35 leftward
    for i in range(min(lit_per_side, config.SEG2_COUNT)):
        pos = config.LIGHTBAR_TOTAL - 1 - i
        colors[pos] = _ZONE_COLORS[pos]

    return colors


def _ease_in_out(t: float) -> float:
    return t * t * (3 - 2 * t)


class LightbarRenderer:
    """
    Renders telemetry / DS4 / CS2 effects to Seg 1 + Seg 2 via HTTP JSON API.
    """

    def __init__(self, wled: WLEDClient) -> None:
        self._wled = wled
        self._flash_phase = False
        self._last_flash_toggle = 0.0
        self._flash_interval = 1.0 / config.REV_FLASH_HZ
        self._pulse_start = 0.0
        self._pulse_period = 1.5
        self._last_sent_colors: List[RGB] = []

    async def run(self) -> None:
        min_interval = 1.0 / config.LIGHTBAR_UPDATE_HZ
        log.info("Lightbar HTTP renderer started (36 LEDs -> Seg 1 & Seg 2).")

        while not state.shutdown_event.is_set():
            loop_start = time.monotonic()

            await self._render_frame()

            elapsed = time.monotonic() - loop_start
            sleep_for = max(0.0, min_interval - elapsed)
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)

        try:
            from ipc_bridge import ipc_bridge
            ipc_bridge.clear()
        except Exception:
            pass
        log.info("Lightbar renderer stopped.")

    async def _render_frame(self) -> None:
        mode = state.lightbar_mode
        color_array: List[RGB] = [_OFF] * config.LIGHTBAR_TOTAL

        if mode == LightbarMode.CS2_FLASH:
            color_array = [_WHITE] * config.LIGHTBAR_TOTAL

        elif mode == LightbarMode.CS2_PULSE:
            if self._pulse_start == 0.0:
                self._pulse_start = time.monotonic()
            elapsed = time.monotonic() - self._pulse_start
            t = (elapsed % self._pulse_period) / self._pulse_period
            brightness = int(_ease_in_out(abs(math.sin(math.pi * t))) * 255)
            color_array = [(brightness, 0, 0)] * config.LIGHTBAR_TOTAL

        elif mode == LightbarMode.DS4_ACTIVE:
            r, g, b = state.get_lightbar_rgb()
            color_array = [(r, g, b)] * config.LIGHTBAR_TOTAL

        elif mode == LightbarMode.REV_METER:
            rpm = state.rpm_pct
            now = time.monotonic()
            if rpm >= config.REV_LIMITER_PCT:
                if now - self._last_flash_toggle >= self._flash_interval:
                    self._flash_phase = not self._flash_phase
                    self._last_flash_toggle = now
                color_array = _build_rev_meter_array(rpm, flash_blue=self._flash_phase)
            else:
                self._flash_phase = False
                color_array = _build_rev_meter_array(rpm, flash_blue=False)

        else:  # IDLE
            color_array = [_OFF] * config.LIGHTBAR_TOTAL

        # Send to C++ IPC bridge and WLED
        if color_array != self._last_sent_colors:
            self._last_sent_colors = list(color_array)
            state.update_lightbar_colors(color_array)
            try:
                from ipc_bridge import ipc_bridge
                if mode in (LightbarMode.CS2_FLASH, LightbarMode.CS2_PULSE):
                    ipc_bridge.update_raw_lightbar(color_array[:17], color_array[17:])
                elif mode == LightbarMode.IDLE and not state.is_ds4_active() and state.rpm_pct <= 0:
                    ipc_bridge.clear()
            except Exception:
                pass
            await self._wled.set_lightbar(color_array)

        await self._update_mode()

    async def _update_mode(self) -> None:
        mode = state.lightbar_mode
        if mode in (LightbarMode.CS2_FLASH, LightbarMode.CS2_PULSE):
            return

        ds4_on = state.is_ds4_active(config.DS4_LIGHTBAR_TIMEOUT)
        # REV_METER activates for ANY positive rpm from telemetry.
        # rpm_pct > 0 means a sim racing game is actively sending data.
        # The visual bar itself only lights up above REV_START_PCT — this is just
        # the mode gate so the lightbar is owned by the rev meter while in a session.
        racing = state.rpm_pct > 0.0

        if ds4_on:
            if mode != LightbarMode.DS4_ACTIVE:
                await state.set_mode(LightbarMode.DS4_ACTIVE)
        elif racing:
            if mode != LightbarMode.REV_METER:
                await state.set_mode(LightbarMode.REV_METER)
        else:
            if mode != LightbarMode.IDLE:
                await state.set_mode(LightbarMode.IDLE)


async def run(wled: WLEDClient) -> None:
    renderer = LightbarRenderer(wled)
    await renderer.run()
