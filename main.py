"""
main.py — RoomLights entry point.

Boots the asyncio event loop and starts all modules as concurrent tasks:
  - Virtual DS4 controller (lightbar interception)
  - Lightbar renderer (Seg 1 + Seg 2 state machine)
  - Sim racing telemetry reader (rev meter fallback)
  - CS2 GSI HTTP server
  - Pomodoro timer
  - Tuya ambient ceiling light

Usage:
    python main.py

Stop with Ctrl+C.
"""

import asyncio
import logging
import signal
import sys

from wled_api import WLEDClient
from state import state
import mod_dualsense
import mod_lightbar
import mod_a_simracing
import mod_b_cs2_gsi
import mod_d_pomodoro
import mod_e_tuya

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

        # Collect all async tasks
        tasks = [
            asyncio.create_task(
                mod_lightbar.run(wled),
                name="lightbar",
            ),
            asyncio.create_task(
                mod_a_simracing.run(),
                name="simracing",
            ),
            asyncio.create_task(
                mod_b_cs2_gsi.run(),
                name="cs2_gsi",
            ),
            asyncio.create_task(
                mod_d_pomodoro.run(wled),
                name="pomodoro",
            ),
            asyncio.create_task(
                mod_e_tuya.run(),
                name="tuya",
            ),
        ]

        # DS4 keepalive task (waits for shutdown then cleans up)
        if ds4_ok:
            tasks.append(
                asyncio.create_task(
                    mod_dualsense.run(ds4_controller),
                    name="ds4",
                )
            )

        log.info("All modules started. Press Ctrl+C to stop.")
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
