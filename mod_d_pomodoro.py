"""
mod_d_pomodoro.py - 25-minute Pomodoro timer on Seg 3.

Seg 3 (WLED ID 3, 6 LEDs, vertical on wall, top-to-bottom) shows WLED's
built-in 'Percent' effect (fx: 98). The effect renders a lit bar that
shrinks as the timer counts down:
  Start:  ix=255 -> all 6 LEDs lit (full bar at top)
  End:    ix=0   -> 0 LEDs lit (empty bar = timer expired)

Updates every 2 seconds to avoid hammering WLED.

Also sets state.context to POMODORO while running, triggering the Tuya
ameent to dim to a warm red.

Usage from main.py:
    asyncio.create_task(mod_d_pomodoro.run(wled))

The Pomodoro runs once and stops. To restart, the user can Ctrl+C and
restart the whole app, or a future version can add a hotkey.
"""

import asyncio
import logging
import time

import config
from state import state, AppContext
from wled_api import WLEDClient

log = logging.getLogger("pomodoro")

# Pomodoro color: warm orange-red for focus mode
_POMODORO_COLOR = (255, 60, 0)


async def run(wled: WLEDClient) -> None:
    """
    Run a single Pomodoro session:
      1. Set Seg 3 to Percent effect (fx=98), counting down over 25 min.
      2. Update WLED every 2 seconds.
      3. Signal Tuya via state.context = POMODORO.
      4. When done: turn Seg 3 off, restore context to IDLE.
    """
    duration = config.POMODORO_DURATION_SEC
    update_interval = config.POMODORO_UPDATE_INTERVAL

    log.info("Pomodoro started: %d minutes.", config.POMODORO_DURATION_MIN)

    # Signal Tuya ambient
    state.pomodoro_active = True
    await state.set_context(AppContext.POMODORO)

    start_time = time.monotonic()
    last_update = 0.0

    while not state.shutdown_event.is_set():
        now = time.monotonic()
        elapsed = now - start_time
        remaining = duration - elapsed

        if remaining <= 0:
            break

        # Throttle WLED updates
        if now - last_update >= update_interval:
            # ix = 255 at start, 0 at end (remaining fraction * 255)
            pct = remaining / duration
            ix = int(pct * 255)
            ix = max(0, min(255, ix))

            state.pomodoro_remaining_pct = pct

            await wled.set_effect(
                seg_id=config.SEG3_ID,
                fx=98,          # FX_MODE_PERCENT
                ix=ix,
                sx=0,           # Speed unused for Percent effect
                col=_POMODORO_COLOR,
                brightness=200,
            )
            log.debug("Pomodoro: %.1f min remaining (ix=%d)",
                      remaining / 60, ix)
            last_update = now

        # Sleep until next update or shutdown
        sleep_for = min(
            update_interval - (time.monotonic() - last_update),
            remaining,
        )
        if sleep_for > 0:
            try:
                await asyncio.wait_for(
                    state.shutdown_event.wait(),
                    timeout=sleep_for,
                )
                break  # Shutdown triggered
            except asyncio.TimeoutError:
                pass   # Normal - keep going

    # Timer finished or shutdown
    state.pomodoro_active = False
    state.pomodoro_remaining_pct = 0.0
    await state.set_context(AppContext.IDLE)

    # Turn off Seg 3
    await wled.set_segment_off(config.SEG3_ID)

    if not state.shutdown_event.is_set():
        log.info("Pomodoro complete! Take a break.")
    else:
        log.info("Pomodoro stopped (shutdown).")
