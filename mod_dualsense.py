"""
mod_dualsense.py - Dynamic Virtual DS4 controller + lightbar interception.

Auto-detects when a PlayStation lightbar game (GTA V, Cyberpunk, Spider-Man,
Death Stranding, etc.) is launched and dynamically attaches the virtual DS4
controller AFTER your physical Xbox controller has already claimed Index 0.

When the game closes, it automatically detaches the virtual DS4 so your
Xbox controller remains completely uninterrupted in Assetto Corsa and other games.
"""

import asyncio
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
from typing import Optional, Set

try:
    import vgamepad as vg
    VGAMEPAD_AVAILABLE = True
except ImportError:
    VGAMEPAD_AVAILABLE = False

import config
from state import state

log = logging.getLogger("dualsense")

# List of known PC games with native DualShock 4 / DualSense lightbar support
_DS4_GAMES: Set[str] = {
    "gta5.exe",
    "cyberpunk2077.exe",
    "spiderman.exe",
    "spidermanmilesmorales.exe",
    "deathstranding.exe",
    "ds.exe",
    "horizonzerodawn.exe",
    "horizonforbiddenwest.exe",
    "gow.exe",
    "godofwar.exe",
    "thelastofus.exe",
    "tlou-i.exe",
    "ratchet.exe",
    "uncharted.exe",
    "u4.exe",
    "daysgone.exe",
    "ghostoftsushima.exe",
    "returnal.exe",
    "helldivers2.exe",
    "detroitbecomehuman.exe",
    "untildawn.exe",
}


# ---------------------------------------------------------------------------
# Win32 Process Snapshot Helper (ultra-fast < 0.2ms check)
# ---------------------------------------------------------------------------
class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.wintypes.DWORD),
        ("cntUsage", ctypes.wintypes.DWORD),
        ("th32ProcessID", ctypes.wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", ctypes.wintypes.DWORD),
        ("cntThreads", ctypes.wintypes.DWORD),
        ("th32ParentProcessID", ctypes.wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("szExeFile", ctypes.c_char * 260),
    ]


def is_ds4_game_running() -> bool:
    """Check if any game with PS lightbar support is currently running."""
    TH32CS_SNAPPROCESS = 0x00000002
    kernel32 = ctypes.windll.kernel32
    h_snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if h_snapshot == -1 or h_snapshot == 0:
        return False

    pe = PROCESSENTRY32()
    pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
    found = False

    if kernel32.Process32First(h_snapshot, ctypes.byref(pe)):
        while True:
            exe_name = pe.szExeFile.decode("utf-8", errors="ignore").lower()
            if exe_name in _DS4_GAMES:
                found = True
                break
            if not kernel32.Process32Next(h_snapshot, ctypes.byref(pe)):
                break

    kernel32.CloseHandle(h_snapshot)
    return found


# ---------------------------------------------------------------------------
# ViGEmClient ctypes structures
# ---------------------------------------------------------------------------
class DS4_LIGHTBAR_COLOR(Structure):
    _fields_ = [
        ("Red",   c_ubyte),
        ("Green", c_ubyte),
        ("Blue",  c_ubyte),
    ]


DS4_NOTIFICATION_CB = CFUNCTYPE(
    None,
    c_void_p,
    c_void_p,
    c_ubyte,
    c_ubyte,
    DS4_LIGHTBAR_COLOR,
    c_void_p,
)


def _find_vigem_dll() -> Optional[Path]:
    import os
    candidates = []
    if VGAMEPAD_AVAILABLE:
        pkg_dir = Path(vg.__file__).parent
        candidates.extend(pkg_dir.rglob("ViGEmClient.dll"))

    prog_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    sys_root   = os.environ.get("SystemRoot", r"C:\Windows")

    candidates += [
        Path(prog_files) / "ViGEm" / "ViGEmClient.dll",
        Path(sys_root) / "System32" / "ViGEmClient.dll",
        Path(sys_root) / "SysWOW64" / "ViGEmClient.dll",
    ]

    for path in candidates:
        if path.exists():
            return path
    return None


class VirtualDS4Controller:
    """Manages dynamic lifetime of the virtual DS4 device."""

    def __init__(self) -> None:
        self._gamepad: Optional[object] = None
        self._vigem: Optional[CDLL] = None
        self._client: Optional[c_void_p] = None
        self._target: Optional[c_void_p] = None
        self._cb_ref = None
        self._is_active = False

    @property
    def is_active(self) -> bool:
        return self._is_active

    def start(self) -> bool:
        if self._is_active:
            return True
        if not VGAMEPAD_AVAILABLE:
            return False

        try:
            self._gamepad = vg.VDS4Gamepad()
            log.info("Virtual DS4 attached dynamically.")
        except Exception as exc:
            log.warning("Could not attach virtual DS4: %s", exc)
            return False

        dll_path = _find_vigem_dll()
        if dll_path is None:
            return False

        try:
            self._vigem = ctypes.CDLL(str(dll_path))
            self._client = self._gamepad._client  # type: ignore[attr-defined]
            self._target = self._gamepad._target  # type: ignore[attr-defined]
        except Exception:
            return False

        cb = DS4_NOTIFICATION_CB(self._on_ds4_notification)
        self._cb_ref = cb

        self._vigem.vigem_target_ds4_register_notification.argtypes = [
            c_void_p,
            c_void_p,
            DS4_NOTIFICATION_CB,
            c_void_p,
        ]
        err = self._vigem.vigem_target_ds4_register_notification(
            self._client, self._target, cb, None
        )
        if err != 0:
            return False

        self._is_active = True
        log.info("Virtual DS4 listening for game lightbar output.")
        return True

    def stop(self) -> None:
        if not self._is_active:
            return
        self._is_active = False
        if self._vigem and self._client and self._target:
            try:
                self._vigem.vigem_target_ds4_unregister_notification(self._target)
            except Exception:
                pass
        self._gamepad = None
        log.info("Virtual DS4 detached.")

    def _on_ds4_notification(
        self,
        client: c_void_p,
        target: c_void_p,
        large_motor: int,
        small_motor: int,
        lightbar: DS4_LIGHTBAR_COLOR,
        user_data: c_void_p,
    ) -> None:
        state.set_lightbar_rgb(lightbar.Red, lightbar.Green, lightbar.Blue)


# ---------------------------------------------------------------------------
# Dynamic background supervisor loop
# ---------------------------------------------------------------------------
async def run(controller: VirtualDS4Controller) -> None:
    """
    Supervises the DS4 controller:
    - Auto-starts when a supported game is launched.
    - Auto-stops when the game closes.
    """
    log.info("DS4 auto-detector running (waiting for PlayStation lightbar games)...")

    while not state.shutdown_event.is_set():
        game_active = await asyncio.to_thread(is_ds4_game_running)

        if config.ENABLE_VIRTUAL_DS4 or game_active:
            if not controller.is_active:
                await asyncio.to_thread(controller.start)
        else:
            if controller.is_active:
                await asyncio.to_thread(controller.stop)

        await asyncio.sleep(2.0)

    await asyncio.to_thread(controller.stop)
    log.info("DS4 module stopped.")
