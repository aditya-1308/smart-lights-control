"""
state.py — Thread-safe shared state for RoomLights.

All modules read/write through this singleton. Uses asyncio.Lock for coroutine
safety and threading.Lock for the DS4 callback thread.

Never import os.environ or config values directly in modules — read state here.
"""

import asyncio
import threading
import time
from enum import Enum, auto
from typing import Optional, Tuple


class LightbarMode(Enum):
    """Current operating mode for the unified Seg 1 + Seg 2 lightbar."""
    IDLE = auto()          # no game active — segs off or dim
    DS4_ACTIVE = auto()    # DS4 lightbar data is being received
    REV_METER = auto()     # telemetry-driven rev meter (fallback)
    CS2_FLASH = auto()     # all-white flash event (2 seconds)
    CS2_PULSE = auto()     # red breathing pulse (low health)


class AppContext(Enum):
    """High-level context for the Tuya ambient ceiling light."""
    IDLE = auto()
    POMODORO = auto()
    CS2 = auto()
    RACING = auto()
    GENERIC_GAME = auto()


class SharedState:
    """
    Centralized state shared across all modules.

    Thread-safe:
      - ``_thread_lock`` guards writes from the DS4 callback thread.
      - ``_async_lock`` guards writes from asyncio coroutines.
      - Reads are lock-free (atomic on CPython due to GIL for simple attrs).
    """

    def __init__(self) -> None:
        # -- Locks --
        self._thread_lock = threading.Lock()
        self._async_lock = asyncio.Lock()

        # -- Lightbar --
        self.lightbar_mode: LightbarMode = LightbarMode.IDLE
        self._pre_flash_mode: Optional[LightbarMode] = None  # saved before CS2 flash

        # -- DS4 lightbar color (written by DS4 callback thread) --
        self._lightbar_rgb: Tuple[int, int, int] = (0, 0, 0)
        self._lightbar_last_update: float = 0.0  # time.monotonic()

        # -- Rev meter --
        self.rpm_pct: float = 0.0  # 0.0 – 1.0

        # -- Context --
        self.context: AppContext = AppContext.IDLE

        # -- Pomodoro --
        self.pomodoro_active: bool = False
        self.pomodoro_remaining_pct: float = 1.0  # 1.0 = full, 0.0 = expired

        # -- CS2 --
        self.cs2_connected: bool = False   # True if GSI POST received recently
        self.cs2_health: int = 100
        self.cs2_flashed: int = 0          # 0–255

        # -- Shutdown --
        self.shutdown_event: asyncio.Event = asyncio.Event()

    # -----------------------------------------------------------------
    # DS4 lightbar — called from a non-asyncio thread (ViGEm callback)
    # -----------------------------------------------------------------
    def set_lightbar_rgb(self, r: int, g: int, b: int) -> None:
        """Set lightbar color from the DS4 callback thread (thread-safe)."""
        with self._thread_lock:
            self._lightbar_rgb = (r, g, b)
            self._lightbar_last_update = time.monotonic()

    def get_lightbar_rgb(self) -> Tuple[int, int, int]:
        """Read the most recent DS4 lightbar color (lock-free read)."""
        return self._lightbar_rgb

    def lightbar_age(self) -> float:
        """Seconds since the last DS4 lightbar update."""
        last = self._lightbar_last_update
        if last == 0.0:
            return float("inf")
        return time.monotonic() - last

    def is_ds4_active(self, timeout: float = 3.0) -> bool:
        """True if DS4 lightbar data arrived within ``timeout`` seconds
        and the color is not black (0, 0, 0)."""
        if self.lightbar_age() > timeout:
            return False
        r, g, b = self._lightbar_rgb
        return (r + g + b) > 0

    # -----------------------------------------------------------------
    # CS2 flash — save/restore previous mode
    # -----------------------------------------------------------------
    async def enter_cs2_flash(self) -> None:
        """Enter CS2 white-flash mode, saving the current lightbar mode."""
        async with self._async_lock:
            if self.lightbar_mode != LightbarMode.CS2_FLASH:
                self._pre_flash_mode = self.lightbar_mode
                self.lightbar_mode = LightbarMode.CS2_FLASH

    async def exit_cs2_flash(self) -> None:
        """Restore the lightbar mode that was active before the flash."""
        async with self._async_lock:
            if self._pre_flash_mode is not None:
                self.lightbar_mode = self._pre_flash_mode
                self._pre_flash_mode = None
            else:
                self.lightbar_mode = LightbarMode.IDLE

    async def set_mode(self, mode: LightbarMode) -> None:
        """Set lightbar mode (async-safe)."""
        async with self._async_lock:
            self.lightbar_mode = mode

    async def set_context(self, ctx: AppContext) -> None:
        """Set the ambient context (async-safe)."""
        async with self._async_lock:
            self.context = ctx


# ---------------------------------------------------------------------------
# Module-level singleton — import ``state`` from anywhere.
# ---------------------------------------------------------------------------
state = SharedState()
