"""
mod_chroma_bridge.py - Razer Chroma SDK REST API bridge.

Hosts a local HTTP server on port 54235 that emulates the Razer Chroma
REST API. When a Chroma-enabled game launches, it connects to our server
thinking it's Razer Synapse, and sends keyboard/mouse RGB color grids.

We receive the 6x22 keyboard grid (132 BGR integer cells) and map it
to the 109-LED Seg 0 strip.

Supports 150+ Chroma-enabled games including: Cyberpunk 2077, Apex Legends,
Fortnite, Overwatch 2, Diablo IV, Rainbow Six Siege, Far Cry 6, etc.

Prerequisite: Razer Synapse must NOT be running (it would claim port 54235).
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from aiohttp import web

import config
from state import state, Seg0Source, AppContext

log = logging.getLogger("chroma_bridge")

RGB = Tuple[int, int, int]

# Chroma keyboard grid dimensions
_KEYBOARD_ROWS = 6
_KEYBOARD_COLS = 22

# Session management
_session_active = False
_session_id = 0
_last_heartbeat = 0.0


def _bgr_int_to_rgb(val: int) -> RGB:
    """Convert Chroma BGR integer (0x00BBGGRR) to (R, G, B) tuple."""
    r = val & 0xFF
    g = (val >> 8) & 0xFF
    b = (val >> 16) & 0xFF
    return (r, g, b)


def _keyboard_grid_to_seg0(grid: List[List[int]]) -> List[RGB]:
    """
    Map a 6x22 Chroma keyboard color grid to 109 Seg 0 LEDs.

    Strategy:
      - Average all 6 rows per column -> 22 column colors
      - Interpolate 22 columns across 109 LEDs for smooth gradient
      - Map left columns to left side of strip, right to right side

    Physical mapping:
      Column 0 (Esc/Ctrl) = far left of strip
      Column 21 (right edge) = far right of strip

    Seg 0 layout (clockwise from bottom-middle):
      idx  0-17  : bottom-right -> map to columns 11-21 (right half)
      idx 18-35  : right edge   -> map to columns 18-21 (rightmost)
      idx 36-71  : top edge     -> map to columns 21-0 (right to left)
      idx 72-89  : left edge    -> map to columns 0-3 (leftmost)
      idx 90-108 : bottom-left  -> map to columns 0-10 (left half)
    """
    if not grid or len(grid) < _KEYBOARD_ROWS:
        return [(0, 0, 0)] * config.SEG0_COUNT

    # Step 1: Average all 6 rows per column -> 22 column colors
    col_colors = []
    for col_idx in range(_KEYBOARD_COLS):
        r_sum, g_sum, b_sum = 0, 0, 0
        count = 0
        for row_idx in range(min(len(grid), _KEYBOARD_ROWS)):
            row = grid[row_idx]
            if col_idx < len(row):
                rgb = _bgr_int_to_rgb(row[col_idx])
                r_sum += rgb[0]
                g_sum += rgb[1]
                b_sum += rgb[2]
                count += 1
        if count > 0:
            col_colors.append((r_sum // count, g_sum // count, b_sum // count))
        else:
            col_colors.append((0, 0, 0))

    # Step 2: Interpolate 22 columns across 109 LEDs
    # Create a smooth gradient by interpolating between column colors
    col_array = np.array(col_colors, dtype=np.float32)  # (22, 3)
    x_cols = np.linspace(0, 21, 22)
    x_leds = np.linspace(0, 21, 109)

    interpolated = np.zeros((109, 3), dtype=np.float32)
    for ch in range(3):
        interpolated[:, ch] = np.interp(x_leds, x_cols, col_array[:, ch])

    # Step 3: Map interpolated linear strip to physical Seg 0 layout
    # The interpolated array is left(0) to right(108) across the monitor.
    # Seg 0 physical layout:
    #   bottom-right (0-17):  screen center-right to right edge
    #   right (18-35):        screen right edge, bottom to top
    #   top (36-71):          screen top, right to left
    #   left (72-89):         screen left edge, top to bottom
    #   bottom-left (90-108): screen left edge to center-left

    seg0_colors: List[RGB] = []
    half = 109 // 2  # ~54

    # Bottom-right (idx 0-17, 18 LEDs): center of bottom -> right corner
    for i in range(18):
        pos = int(half + (i / 17) * (108 - half)) if i < 17 else 108
        pos = min(108, max(0, pos))
        c = interpolated[pos].astype(int)
        seg0_colors.append((int(c[0]), int(c[1]), int(c[2])))

    # Right edge (idx 18-35, 18 LEDs): sample rightmost columns
    for i in range(18):
        c = interpolated[min(108, 100 + i // 2)].astype(int)
        seg0_colors.append((int(c[0]), int(c[1]), int(c[2])))

    # Top (idx 36-71, 36 LEDs): right to left across full width
    for i in range(36):
        pos = int(108 - (i / 35) * 108)
        c = interpolated[pos].astype(int)
        seg0_colors.append((int(c[0]), int(c[1]), int(c[2])))

    # Left edge (idx 72-89, 18 LEDs): sample leftmost columns
    for i in range(18):
        c = interpolated[min(8, i // 2)].astype(int)
        seg0_colors.append((int(c[0]), int(c[1]), int(c[2])))

    # Bottom-left (idx 90-108, 19 LEDs): left corner -> center
    for i in range(19):
        pos = int((i / 18) * half)
        pos = min(108, max(0, pos))
        c = interpolated[pos].astype(int)
        seg0_colors.append((int(c[0]), int(c[1]), int(c[2])))

    return seg0_colors[:config.SEG0_COUNT]


# -----------------------------------------------------------------------
# Chroma REST API Handlers
# -----------------------------------------------------------------------

async def _handle_init(request: web.Request) -> web.Response:
    """POST /razer/chromasdk - Game registers a Chroma session."""
    global _session_active, _session_id, _last_heartbeat

    try:
        data = await request.json()
    except Exception:
        data = {}

    title = data.get("title", "Unknown Game")
    log.info("Chroma game connected: '%s'", title)

    _session_id += 1
    _session_active = True
    _last_heartbeat = time.monotonic()
    state.chroma_active = True
    state.chroma_last_heartbeat = _last_heartbeat

    # Take ownership of Seg 0
    await state.set_seg0_source(Seg0Source.CHROMA)
    await state.set_context(AppContext.GENERIC_GAME)

    return web.json_response({
        "sessionid": _session_id,
        "uri": f"http://127.0.0.1:{config.CHROMA_PORT}/chromasdk",
    })


async def _handle_heartbeat(request: web.Request) -> web.Response:
    """PUT /chromasdk/heartbeat - Game keeps session alive."""
    global _last_heartbeat
    _last_heartbeat = time.monotonic()
    state.chroma_last_heartbeat = _last_heartbeat
    return web.json_response({"tick": 1})


async def _handle_keyboard(request: web.Request) -> web.Response:
    """PUT/POST /chromasdk/keyboard - Receive keyboard color grid."""
    global _last_heartbeat
    _last_heartbeat = time.monotonic()

    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"result": 0})

    effect = payload.get("effect", "")

    if effect == "CHROMA_CUSTOM" or effect == "CHROMA_CUSTOM2":
        grid = payload.get("param", [])
        if grid:
            colors = _keyboard_grid_to_seg0(grid)
            state.update_seg0_colors(colors)
    elif effect == "CHROMA_STATIC":
        color_val = payload.get("param", {}).get("color", 0)
        rgb = _bgr_int_to_rgb(color_val)
        state.update_seg0_colors([rgb] * config.SEG0_COUNT)
    elif effect == "CHROMA_NONE":
        state.update_seg0_colors([(0, 0, 0)] * config.SEG0_COUNT)

    return web.json_response({"result": 0})


async def _handle_mouse(request: web.Request) -> web.Response:
    """PUT/POST /chromasdk/mouse - Acknowledge but ignore."""
    global _last_heartbeat
    _last_heartbeat = time.monotonic()
    return web.json_response({"result": 0})


async def _handle_mousepad(request: web.Request) -> web.Response:
    """PUT/POST /chromasdk/mousepad - Acknowledge but ignore."""
    return web.json_response({"result": 0})


async def _handle_chromalink(request: web.Request) -> web.Response:
    """PUT/POST /chromasdk/chromalink - Acknowledge but ignore."""
    return web.json_response({"result": 0})


async def _handle_delete_session(request: web.Request) -> web.Response:
    """DELETE /chromasdk - Game disconnects."""
    global _session_active
    log.info("Chroma game disconnected.")
    _session_active = False
    state.chroma_active = False
    await state.set_seg0_source(Seg0Source.SCREEN_CAPTURE)
    await state.set_context(AppContext.IDLE)
    return web.json_response({"result": 0})


async def _heartbeat_watchdog() -> None:
    """Kill Chroma session if heartbeat stops."""
    global _session_active
    while not state.shutdown_event.is_set():
        await asyncio.sleep(2.0)
        if _session_active:
            age = time.monotonic() - _last_heartbeat
            if age > config.CHROMA_HEARTBEAT_TIMEOUT:
                log.info("Chroma heartbeat timeout (%.1fs). Releasing Seg 0.", age)
                _session_active = False
                state.chroma_active = False
                await state.set_seg0_source(Seg0Source.SCREEN_CAPTURE)
                await state.set_context(AppContext.IDLE)


# -----------------------------------------------------------------------
# Module entry point
# -----------------------------------------------------------------------

async def run() -> None:
    """Start the Chroma REST API server."""
    app = web.Application()

    # Session init
    app.router.add_post("/razer/chromasdk", _handle_init)

    # Session management
    app.router.add_put("/chromasdk/heartbeat", _handle_heartbeat)
    app.router.add_delete("/chromasdk", _handle_delete_session)

    # Device endpoints
    app.router.add_put("/chromasdk/keyboard", _handle_keyboard)
    app.router.add_post("/chromasdk/keyboard", _handle_keyboard)
    app.router.add_put("/chromasdk/mouse", _handle_mouse)
    app.router.add_post("/chromasdk/mouse", _handle_mouse)
    app.router.add_put("/chromasdk/mousepad", _handle_mousepad)
    app.router.add_post("/chromasdk/mousepad", _handle_mousepad)
    app.router.add_put("/chromasdk/chromalink", _handle_chromalink)
    app.router.add_post("/chromasdk/chromalink", _handle_chromalink)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, host="127.0.0.1", port=config.CHROMA_PORT)

    try:
        await site.start()
        log.info("Chroma bridge listening on http://127.0.0.1:%d", config.CHROMA_PORT)
        log.info("150+ Chroma-enabled games will auto-connect.")

        watchdog = asyncio.create_task(_heartbeat_watchdog())
        await state.shutdown_event.wait()
        watchdog.cancel()

    except OSError as exc:
        if "address already in use" in str(exc).lower() or "10048" in str(exc):
            log.error("Port %d in use! Is Razer Synapse running? "
                      "Disable it for Chroma bridge to work.", config.CHROMA_PORT)
        else:
            log.error("Chroma bridge failed: %s", exc)
    finally:
        await runner.cleanup()
        log.info("Chroma bridge stopped.")
