"""
mod_b_cs2_gsi.py - CS2 Game State Integration HTTP server.

Listens on localhost:3000 for JSON POST requests from CS2's built-in
Game State Integration (GSI) system.

Setup: drop gamestate_integration_roomlights.cfg into:
  <Steam>\\steamapps\\common\\Counter-Strike Global Offensive\\game\\csgo\\cfg\\

Triggers:
  player.state.flashed > 0  -> 2-second all-white flash on Seg 1+2, then restore
  player.state.health < 20  -> Red breathing pulse on Seg 1+2 (CS2_PULSE mode)
  Game connected / active   -> sets context to 'cs2' for Tuya ambient
"""

import asyncio
import logging
import time
from typing import Any, Dict

from aiohttp import web

import config
from state import state, AppContext, LightbarMode

log = logging.getLogger("cs2_gsi")

# How long after the last GSI POST to consider CS2 still active
_CS2_TIMEOUT_SEC = 30.0
_last_gsi_time: float = 0.0


async def _handle_cs2_gsi(request: web.Request) -> web.Response:
    """Handle incoming CS2 GSI POST requests."""
    global _last_gsi_time

    # Parse JSON body
    try:
        data: Dict[str, Any] = await request.json()
    except Exception:
        return web.Response(status=400, text="Bad JSON")

    # Validate auth token
    token = data.get("auth", {}).get("token", "")
    if token != config.CS2_GSI_TOKEN:
        log.warning("CS2 GSI: invalid token received")
        return web.Response(status=401, text="Unauthorized")

    _last_gsi_time = time.monotonic()

    # Set context for Tuya ambient
    await state.set_context(AppContext.CS2)

    # Extract player state
    player_state = data.get("player", {}).get("state", {})
    health: int = player_state.get("health", 100)
    flashed: int = player_state.get("flashed", 0)

    state.cs2_health = health
    state.cs2_flashed = flashed
    state.cs2_connected = True

    # --- Trigger: Flashbang ---
    if flashed > 0:
        log.debug("CS2: flashed=%d -> triggering white flash", flashed)
        if state.lightbar_mode != LightbarMode.CS2_FLASH:
            await state.enter_cs2_flash()
            # Schedule restoration after 2 seconds
            asyncio.create_task(_schedule_flash_restore(2.0))

    # --- Trigger: Low health ---
    elif health < 20:
        log.debug("CS2: health=%d -> red pulse", health)
        if state.lightbar_mode not in (LightbarMode.CS2_FLASH,
                                        LightbarMode.CS2_PULSE):
            await state.set_mode(LightbarMode.CS2_PULSE)

    # --- Normal state: restore if we were pulsing ---
    elif state.lightbar_mode == LightbarMode.CS2_PULSE:
        await state.set_mode(LightbarMode.DS4_ACTIVE
                             if state.is_ds4_active()
                             else LightbarMode.IDLE)

    return web.Response(status=200, text="OK")


async def _schedule_flash_restore(delay: float) -> None:
    """Wait ``delay`` seconds then restore the lightbar mode."""
    await asyncio.sleep(delay)
    await state.exit_cs2_flash()
    log.debug("CS2 flash restored.")


async def _cs2_watchdog() -> None:
    """
    Background task: if no GSI POST received in _CS2_TIMEOUT_SEC seconds,
    clear the CS2 context so Tuya reverts to idle.
    """
    while not state.shutdown_event.is_set():
        await asyncio.sleep(5.0)
        if _last_gsi_time > 0:
            age = time.monotonic() - _last_gsi_time
            if age > _CS2_TIMEOUT_SEC:
                if state.cs2_connected:
                    state.cs2_connected = False
                    state.cs2_health = 100
                    state.cs2_flashed = 0
                    await state.set_context(AppContext.IDLE)
                    log.info("CS2 disconnected (no GSI for %.0fs).", age)


async def run() -> None:
    """
    Start the aiohttp GSI web server and the CS2 watchdog.

    Runs until state.shutdown_event is set.
    """
    app = web.Application()
    app.router.add_post("/cs2", _handle_cs2_gsi)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(
        runner,
        host="127.0.0.1",
        port=config.CS2_GSI_PORT,
    )

    try:
        await site.start()
        log.info("CS2 GSI server listening on http://127.0.0.1:%d/cs2",
                 config.CS2_GSI_PORT)

        # Run watchdog as a sibling task
        watchdog = asyncio.create_task(_cs2_watchdog())

        # Wait for shutdown
        await state.shutdown_event.wait()
        watchdog.cancel()

    except OSError as exc:
        log.error("CS2 GSI server failed to start: %s", exc)
        log.error("Is port %d already in use?", config.CS2_GSI_PORT)
    finally:
        await runner.cleanup()
        log.info("CS2 GSI server stopped.")
