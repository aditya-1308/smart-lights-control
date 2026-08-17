"""
mod_dualsense.py - Virtual DS4 controller + lightbar interception.

Creates a virtual DualShock 4 controller via ViGEmBus so that games send
lightbar RGB data to it. Uses ctypes to hook ViGEmClient.dll directly for
the DS4_LIGHTBAR_COLOR struct (bypassing vgamepad's high-level API which
does not expose the raw RGB bytes).

How it works:
  1. vgamepad creates the virtual DS4 device (handles driver lifecycle).
  2. We load ViGEmClient.dll via ctypes and re-register our own callback
     using vigem_target_ds4_register_notification() to receive the full
     DS4_LIGHTBAR_COLOR struct on every HID output report from the game.
  3. Received RGB is written to state.set_lightbar_rgb().
  4. mod_lightbar.py reads state and applies to Seg 1 + Seg 2.

Game compatibility:
  GTA V:       Steam Input OFF - native DirectHID lightbar.
  AC + CSP:    Steam Input OFF - CSP Gamepad FX writes HID directly.
  F1 23/24:    NO lightbar - falls back to rev meter in mod_a_simracing.py.
  CS2:         NO lightbar - handled by mod_b_cs2_gsi.py.
  Cyberpunk:   Steam Input OFF - native DirectHID.
  Sony ports:  Steam Input OFF - native DirectHID.

Prerequisite: ViGEmBus driver installed.
  https://github.com/nefarius/ViGEmBus/releases
"""

import ctypes
import ctypes.wintypes
import logging
import threading
import time
from ctypes import (
    CDLL, CFUNCTYPE, POINTER, Structure,
    c_int, c_ubyte, c_ulong, c_void_p,
)
from pathlib import Path
from typing import Optional

try:
    import vgamepad as vg
    VGAMEPAD_AVAILABLE = True
except ImportError:
    VGAMEPAD_AVAILABLE = False

import config
from state import state

log = logging.getLogger("dualsense")


# ---------------------------------------------------------------------------
# ViGEmClient ctypes structures
# ---------------------------------------------------------------------------
class DS4_LIGHTBAR_COLOR(Structure):
    """Mirrors the C struct DS4_LIGHTBAR_COLOR from ViGEmClient.h."""
    _fields_ = [
        ("Red",   c_ubyte),
        ("Green", c_ubyte),
        ("Blue",  c_ubyte),
    ]


# Callback signature: EVT_VIGEM_DS4_NOTIFICATION
# void callback(client, target, LargeMotor, SmallMotor, LightbarColor, UserData)
DS4_NOTIFICATION_CB = CFUNCTYPE(
    None,           # return void
    c_void_p,       # PVIGEM_CLIENT  Client
    c_void_p,       # PVIGEM_TARGET  Target
    c_ubyte,        # UCHAR          LargeMotor
    c_ubyte,        # UCHAR          SmallMotor
    DS4_LIGHTBAR_COLOR,  # DS4_LIGHTBAR_COLOR  LightbarColor
    c_void_p,       # LPVOID         UserData
)


def _find_vigem_dll() -> Optional[Path]:
    """
    Locate ViGEmClient.dll.

    vgamepad bundles it; fall back to a few well-known install paths.
    """
    candidates = []

    # vgamepad bundles ViGEmClient.dll next to its own package files
    if VGAMEPAD_AVAILABLE:
        pkg_dir = Path(vg.__file__).parent
        candidates.extend(pkg_dir.rglob("ViGEmClient.dll"))

    # Well-known standalone install paths
    candidates += [
        Path(r"C:\Program Files\ViGEm\ViGEmClient.dll"),
        Path(r"C:\Windows\System32\ViGEmClient.dll"),
        Path(r"C:\Windows\SysWOW64\ViGEmClient.dll"),
    ]

    for path in candidates:
        if path.exists():
            return path
    return None


class VirtualDS4Controller:
    """
    Manages the lifetime of the virtual DS4 device and the lightbar callback.

    Usage::

        ctrl = VirtualDS4Controller()
        ctrl.start()       # creates device, registers callback
        # ... run your app ...
        ctrl.stop()        # cleans up
    """

    def __init__(self) -> None:
        self._gamepad: Optional[object] = None   # vg.VDS4Gamepad instance
        self._vigem: Optional[CDLL] = None       # ViGEmClient.dll handle
        self._client: Optional[c_void_p] = None  # PVIGEM_CLIENT
        self._target: Optional[c_void_p] = None  # PVIGEM_TARGET
        self._cb_ref = None  # Keep callback alive (prevent GC)
        self._running = False
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> bool:
        """
        Create the virtual DS4 device and register the lightbar callback.

        Returns True on success, False if ViGEmBus is not installed or
        any other setup error occurs.
        """
        if not VGAMEPAD_AVAILABLE:
            log.error("vgamepad not installed. Run: pip install vgamepad")
            return False

        # 1. Create virtual DS4 via vgamepad (handles driver bookkeeping)
        try:
            self._gamepad = vg.VDS4Gamepad()
            log.info("Virtual DS4 created via vgamepad.")
        except Exception as exc:
            log.error("Failed to create virtual DS4: %s", exc)
            log.error("Is ViGEmBus driver installed? "
                      "https://github.com/nefarius/ViGEmBus/releases")
            return False

        # 2. Load ViGEmClient.dll for raw callback access
        dll_path = _find_vigem_dll()
        if dll_path is None:
            log.error("ViGEmClient.dll not found. "
                      "Install ViGEmBus or pip install vgamepad.")
            return False

        try:
            self._vigem = ctypes.CDLL(str(dll_path))
        except OSError as exc:
            log.error("Could not load ViGEmClient.dll: %s", exc)
            return False

        # 3. Get the internal client/target pointers from vgamepad's objects.
        #    vgamepad stores them as _client and _target attributes (ctypes ptrs).
        try:
            self._client = self._gamepad._client  # type: ignore[attr-defined]
            self._target = self._gamepad._target  # type: ignore[attr-defined]
        except AttributeError:
            # Fallback: allocate our own via the DLL
            log.warning("Could not access vgamepad internals; "
                        "allocating ViGEm client manually.")
            self._vigem.vigem_alloc.restype = c_void_p
            self._client = self._vigem.vigem_alloc()
            if not self._client:
                log.error("vigem_alloc() returned NULL")
                return False
            err = self._vigem.vigem_connect(self._client)
            if err != 0:
                log.error("vigem_connect() failed: 0x%X", err)
                return False
            self._vigem.vigem_target_ds4_alloc.restype = c_void_p
            self._target = self._vigem.vigem_target_ds4_alloc()
            err = self._vigem.vigem_target_add(self._client, self._target)
            if err != 0:
                log.error("vigem_target_add() failed: 0x%X", err)
                return False

        # 4. Register our lightbar callback
        cb = DS4_NOTIFICATION_CB(self._on_ds4_notification)
        self._cb_ref = cb  # Must keep reference to prevent garbage collection!

        self._vigem.vigem_target_ds4_register_notification.argtypes = [
            c_void_p,           # client
            c_void_p,           # target
            DS4_NOTIFICATION_CB, # callback
            c_void_p,           # user data
        ]
        err = self._vigem.vigem_target_ds4_register_notification(
            self._client, self._target, cb, None
        )
        if err != 0:
            log.error("vigem_target_ds4_register_notification() failed: 0x%X", err)
            return False

        self._running = True
        log.info("DS4 lightbar callback registered. Waiting for game data...")
        log.info("IMPORTANT: Disable Steam Input in Steam game properties "
                 "for DirectHID games (GTA V, AC+CSP).")
        return True

    def stop(self) -> None:
        """Unregister callback and clean up."""
        self._running = False
        if self._vigem and self._client and self._target:
            try:
                self._vigem.vigem_target_ds4_unregister_notification(self._target)
            except Exception:
                pass
        # vgamepad handles device removal on GC / __del__
        self._gamepad = None
        log.info("Virtual DS4 stopped.")

    # ------------------------------------------------------------------
    # ViGEm callback - called from Win32 thread pool, NOT asyncio thread
    # ------------------------------------------------------------------
    def _on_ds4_notification(
        self,
        client: c_void_p,
        target: c_void_p,
        large_motor: int,
        small_motor: int,
        lightbar: DS4_LIGHTBAR_COLOR,
        user_data: c_void_p,
    ) -> None:
        """
        Fired by ViGEm whenever the game/Steam writes an HID output report.

        This runs on a Win32 thread, not the asyncio event loop.
        We only write to state (thread-safe via threading.Lock).
        """
        r, g, b = lightbar.Red, lightbar.Green, lightbar.Blue
        state.set_lightbar_rgb(r, g, b)
        # Uncomment for debugging:
        # log.debug("DS4 lightbar: #%02X%02X%02X  rumble=(%d,%d)",
        #           r, g, b, large_motor, small_motor)


# ---------------------------------------------------------------------------
# Module entry point - called from main.py
# ---------------------------------------------------------------------------
async def run(controller: VirtualDS4Controller) -> None:
    """
    Keeps the DS4 controller alive until shutdown.

    The actual work happens in the Win32 callback thread registered by
    ``controller.start()``. This coroutine just waits for shutdown.
    """
    log.info("DS4 module running. Waiting for lightbar data from games...")
    await state.shutdown_event.wait()
    controller.stop()
    log.info("DS4 module stopped.")
