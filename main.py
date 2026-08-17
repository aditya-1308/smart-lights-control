"""
main.py — RoomLights entry point.

Boots the asyncio event loop and starts all modules as concurrent tasks:

  Phase 1 — Lightbar (Seg 1 + Seg 2):
    - Virtual DS4 controller (lightbar interception)
    - Lightbar renderer (DS4 color / rev meter / CS2 flash)
    - Sim racing telemetry reader (AC shared memory / F1 UDP)
    - CS2 GSI HTTP server
    - Pomodoro timer (Seg 3 wall strip)
    - Tuya ambient ceiling light

  Phase 2 — Seg 0 (109-LED screen strip):
    - Seg 0 router (priority-based WLED writer)
    - Screen capture (replaces Prismatik, edge ambient)
    - Chroma bridge (150+ games via Razer Chroma REST API)
    - AC spatial telemetry (flags, track limits, sectors)
    - F1 spatial telemetry (proximity spotter, flags, collisions)
    - CS2 spatial effects (flashbang, bomb, health on Seg 0)
    - Smart ROI (directional damage detection for other games)

Usage:
    python main.py

Stop with Ctrl+C.
"""

import asyncio
import logging
import signal
import sys

import config
from wled_api import WLEDClient
from state import state

# Phase 1 modules
import mod_dualsense
import mod_lightbar
import mod_a_simracing
import mod_b_cs2_gsi
import mod_d_pomodoro
import mod_e_tuya

# Phase 2 modules
import mod_seg0_router
import mod_screen_capture
import mod_chroma_bridge
import mod_spatial_ac
import mod_spatial_f1
import mod_spatial_cs2
import mod_smart_roi

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("main")


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------
def _handle_shutdown(sig_name: str) -> None:
    """Signal handler: trigger clean shutdown via the asyncio event."""
    log.info("Received %s — shutting down...", sig_name)
    state.shutdown_event.set()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main() -> None:
    log.info("=" * 60)
    log.info("  RoomLights starting up")
    log.info("=" * 60)
    log.info("  Seg 0 screen capture: %d FPS", config.SCREEN_CAPTURE_FPS)
    log.info("  Chroma bridge:        port %d", config.CHROMA_PORT)
    log.info("  CS2 GSI:              port %d", config.CS2_GSI_PORT)
    log.info("  Sim game:             %s", config.SIM_GAME)
    log.info("=" * 60)

    # Register OS signals for graceful shutdown (Ctrl+C / task kill)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(
                sig, lambda s=sig: _handle_shutdown(s.name)
            )
        except NotImplementedError:
            # Windows doesn't support add_signal_handler for all signals;
            # Ctrl+C is handled via KeyboardInterrupt catch below.
            pass

    # ------------------------------------------------------------------
    # Start virtual DS4 controller (background thread via Win32 callback)
    # ------------------------------------------------------------------
    ds4_controller = mod_dualsense.VirtualDS4Controller()
    ds4_ok = ds4_controller.start()
    if not ds4_ok:
        log.warning("Virtual DS4 failed to start — "
                    "DS4 lightbar mode unavailable. Rev meter will be used.")

    # ------------------------------------------------------------------
    # Create the shared WLED HTTP client (one session for all modules)
    # ------------------------------------------------------------------
    async with WLEDClient() as wled:
        await wled.start()

        tasks = [
            # ----------------------------------------------------------
            # Phase 1: Lightbar (Seg 1 + Seg 2) + Pomodoro + Tuya
            # ----------------------------------------------------------
            asyncio.create_task(mod_lightbar.run(wled),      name="lightbar"),
            asyncio.create_task(mod_a_simracing.run(),        name="simracing"),
            asyncio.create_task(mod_b_cs2_gsi.run(),          name="cs2_gsi"),
            asyncio.create_task(mod_d_pomodoro.run(wled),     name="pomodoro"),
            asyncio.create_task(mod_e_tuya.run(),             name="tuya"),

            # ----------------------------------------------------------
            # Phase 2: Seg 0 (109 LEDs) spatial system
            # ----------------------------------------------------------
            # Router: reads state.seg0_colors and sends to WLED at target FPS
            asyncio.create_task(mod_seg0_router.run(wled),   name="seg0_router"),

            # Screen capture: edge ambient — lowest priority fallback
            asyncio.create_task(mod_screen_capture.run(),    name="screen_capture"),

            # Chroma bridge: intercepts 150+ games' RGB data
            asyncio.create_task(mod_chroma_bridge.run(),     name="chroma_bridge"),

            # AC spatial: flags, track limits, sector flashes
            asyncio.create_task(mod_spatial_ac.run(),        name="spatial_ac"),

            # CS2 spatial: flashbang, low health, bomb timer on Seg 0
            asyncio.create_task(mod_spatial_cs2.run(),       name="spatial_cs2"),

            # Smart ROI: directional damage detection for any game
            asyncio.create_task(mod_smart_roi.run(),         name="smart_roi"),
        ]

        # F1 spatial only boots when SIM_GAME=F1
        if config.SIM_GAME == "F1":
            tasks.append(
                asyncio.create_task(mod_spatial_f1.run(), name="spatial_f1")
            )

        # DS4 keepalive task (waits for shutdown then cleans up)
        if ds4_ok:
            tasks.append(
                asyncio.create_task(
                    mod_dualsense.run(ds4_controller), name="ds4"
                )
            )

        log.info("All %d modules started. Press Ctrl+C to stop.", len(tasks))
        log.info("-" * 60)

        # Wait until shutdown is signalled
        await state.shutdown_event.wait()

        log.info("Cancelling all tasks...")
        for task in tasks:
            task.cancel()

        # Wait for all tasks to finish cancellation
        await asyncio.gather(*tasks, return_exceptions=True)

    log.info("RoomLights stopped. Goodbye.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # Windows Ctrl+C fallback
        state.shutdown_event.set()
        log.info("Stopped via KeyboardInterrupt.")
