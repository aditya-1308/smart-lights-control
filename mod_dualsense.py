"""
mod_dualsense.py - Dynamic Virtual DS4 controller + lightbar interception.

Auto-detects when ANY PlayStation PC port or lightbar-supported game is launched
and dynamically attaches the virtual DS4 controller.

CRITICAL: When ANY Sim Racing game (Assetto Corsa, F1, AMS2, Forza, iRacing, rFactor,
Le Mans Ultimate, WRC, BeamNG, etc.) or its companion launchers/tools are running,
the virtual DS4 controller is STRICTLY PREVENTED from attaching and is immediately
detached so physical Xbox controllers and racing wheels work uninterrupted.
"""

import asyncio
import ctypes
import ctypes.wintypes
import logging
import mmap
import time
from ctypes import (
    CDLL, CFUNCTYPE, POINTER, Structure,
    c_int, c_ubyte, c_ulong, c_uint, c_void_p,
)
from pathlib import Path
from typing import Optional, Set

try:
    import vgamepad as vg
    VGAMEPAD_AVAILABLE = True
except ImportError:
    VGAMEPAD_AVAILABLE = False

try:
    import win32gui
    import win32process
    PYWIN32_AVAILABLE = True
except ImportError:
    PYWIN32_AVAILABLE = False

import config
from state import state

log = logging.getLogger("dualsense")

# ===========================================================================
# Sim racing games & launchers (NEVER attach virtual DS4)
# ===========================================================================
_SIM_RACING_GAMES: Set[str] = {
    # Assetto Corsa & Content Manager
    "acs.exe",
    "acs_x86.exe",
    "acs_pro.exe",
    "assettocorsa.exe",
    "assetto_corsa.exe",
    "contentmanager.exe",
    "content manager.exe",
    "acmanager.exe",
    "ac_launcher.exe",
    "acc.exe",
    "accserver.exe",
    "assettocorsacompetizione.exe",
    "acc-win64-shipping.exe",
    "assettocorsaevo.exe",
    "assettocorsaevo-win64-shipping.exe",
    "ace.exe",
    "ac2.exe",

    # F1 Series (EA Sports / Codemasters 2018 - 2026+)
    "f1_2018.exe", "f1_2019.exe", "f1_2020.exe", "f1_2021.exe",
    "f1_2022.exe", "f1_2023.exe", "f1_2024.exe", "f1_2025.exe", "f1_2026.exe",
    "f12018.exe", "f12019.exe", "f12020.exe", "f12021.exe",
    "f12022.exe", "f12023.exe", "f12024.exe", "f12025.exe", "f12026.exe",
    "f1_18.exe", "f1_19.exe", "f1_20.exe", "f1_21.exe",
    "f1_22.exe", "f1_23.exe", "f1_24.exe", "f1_25.exe", "f1_26.exe",
    "f122.exe", "f123.exe", "f124.exe", "f125.exe", "f126.exe",
    "f1.exe",

    # Automobilista & Project CARS
    "ams2avx.exe", "ams2.exe", "automobilista2.exe",
    "ams.exe", "automobilista.exe",
    "pcars.exe", "pcars64.exe", "pcars2.exe", "pcars2avx.exe", "pcars3.exe", "pcars3avx.exe",

    # Forza Horizon & Motorsport
    "forzahorizon5.exe", "forzahorizon4.exe", "forzahorizon3.exe",
    "forzamotorsport.exe", "forzamotorsport7.exe", "forzamotorsport6.exe",
    "forza_gaming.desktop.x64_release_final.exe",

    # iRacing
    "iracingsim64dx11.exe", "iracingsim64.exe", "iracingsim.exe", "iracingsim32.exe",
    "iracingui.exe", "iracingsim64directx11.exe", "iracingservice.exe",

    # rFactor & Le Mans Ultimate
    "rfactor2.exe", "rfactor.exe", "rfactor2dedicated.exe", "rfactor 2.exe",
    "lemansultimate.exe", "lmu.exe", "lemansultimate_x64.exe",

    # DiRT & WRC Rally Series
    "dirtrally2.exe", "dirt_rally_2.exe", "dirtrally.exe", "dirt_rally.exe",
    "dirt2_game.exe", "dirt3_game.exe", "dirt4.exe", "dirt5.exe", "dirt 5.exe",
    "eawrc.exe", "wrc.exe", "wrc23.exe", "wrc24.exe", "wrc10.exe", "wrc9.exe", "wrc8.exe", "wrc7.exe",
    "wrcgenerations.exe", "wrc generations.exe",
    "richardburnsrally_sse.exe", "richardburnsrally.exe", "richard burns rally.exe", "rbr.exe",
    "rallysimfans.exe", "rsfrbr.exe",
    "dakardesertrally.exe", "dakardesertrally-win64-shipping.exe", "dakar.exe",

    # Physics Sims & Others
    "beamng.drive.x64.exe", "beamng.drive.exe", "beamng.drive.x86.exe", "beamng.drive.directx11.x64.exe",
    "raceroom.exe", "raceroom64.exe", "rrre.exe", "rrre64.exe",
    "kartkraft.exe", "kartkraft-win64-shipping.exe",
    "lfs.exe", "liveforspeed.exe", "kartsim.exe",

    # Sim Hardware / Companion Tools
    "simhub.exe", "simhubwpf.exe",
    "fanatec_control_panel.exe", "fanalab.exe",
    "moza pit house.exe", "mozapithouse.exe",
    "truedrive.exe", "simucube.exe",
    "thrustmaster.exe", "tmcontrolpanel.exe",
}

# Window title keywords to identify sim racing games even with custom / modded exes
_SIM_RACING_TITLE_KEYWORDS = (
    "assetto corsa",
    "content manager",
    "automobilista",
    "project cars",
    "iracing",
    "forza motorsport",
    "forza horizon",
    "rfactor",
    "le mans ultimate",
    "dirt rally",
    "ea sports wrc",
    "beamng",
    "raceroom",
    "richard burns rally",
    "rallysimfans",
    "live for speed",
    "kartkraft",
    "f1 20",
    "f1 22", "f1 23", "f1 24", "f1 25", "f1 26",
)

# Known game executables with native DualShock 4 / DualSense lightbar or controller support
_KNOWN_GAMES: Set[str] = {
    # Sony PlayStation PC ports
    "spiderman.exe",
    "spidermanmilesmorales.exe",
    "spiderman2.exe",
    "gow.exe",
    "godofwar.exe",
    "godofwarragnarok.exe",
    "thelastofus.exe",
    "tlou-i.exe",
    "thelastofusparti.exe",
    "horizonzerodawn.exe",
    "horizonforbiddenwest.exe",
    "ghostoftsushima.exe",
    "daysgone.exe",
    "deathstranding.exe",
    "ds.exe",
    "returnal.exe",
    "ratchet.exe",
    "uncharted.exe",
    "u4.exe",
    "helldivers2.exe",
    "untildawn.exe",
    "detroitbecomehuman.exe",
    "beyondtwosouls.exe",
    "heavyrain.exe",
    "sackboy.exe",

    # Rockstar
    "gta5.exe",
    "gtav.exe",
    "rdr2.exe",
    "reddeadredemption2.exe",

    # CD Projekt Red
    "cyberpunk2077.exe",
    "witcher3.exe",

    # Ubisoft
    "farcry5.exe",
    "farcry6.exe",
    "acvalhalla.exe",
    "acmirage.exe",
    "acshadows.exe",
    "watchdogslegion.exe",
    "thecrew2.exe",
    "thecrew-motorfest.exe",
    "avatar_fop.exe",

    # EA / Sports / Action
    "fc24.exe",
    "fc25.exe",
    "fifa23.exe",
    "fifa22.exe",
    "nfsunbound.exe",
    "nfsheat.exe",
    "jedifallenorder.exe",
    "jedisurvivor.exe",
    "deadspace.exe",
    "apexlegends.exe",
    "r5apex.exe",

    # Capcom / Square Enix / Bandai / Others
    "re4.exe",
    "re7.exe",
    "re8.exe",
    "re2.exe",
    "re3.exe",
    "re9.exe",
    "village.exe",
    "ff7remake_.exe",
    "ff7rebirth.exe",
    "ffxvi.exe",
    "ffxv.exe",
    "monsterhunterwilds.exe",
    "monsterhunterworld.exe",
    "monsterhunterrise.exe",
    "eldenring.exe",
    "armoredcore6.exe",
    "sekiro.exe",
    "darksouls3.exe",
    "liesofp.exe",
    "blackmythwukong.exe",
    "wukong.exe",
    "control.exe",
    "alanwake2.exe",
    "deathloop.exe",
    "ghostwiretokyo.exe",
    "sifu.exe",
    "hifirush.exe",
    "metroexodus.exe",
    "warframe.x64.exe",
    "warframe.exe",
    "cs2.exe",
    "csgo.exe",
    "valorant.exe",
    "overwatch.exe",
    "fortniteclient-win64-shipping.exe",
}

# Non-game system and desktop processes to ignore during foreground window checks
_IGNORED_PROCESSES: Set[str] = {
    "explorer.exe",
    "taskmgr.exe",
    "cmd.exe",
    "powershell.exe",
    "pwsh.exe",
    "devenv.exe",
    "code.exe",
    "sublime_text.exe",
    "notepad.exe",
    "notepad++.exe",
    "calc.exe",
    "systemsettings.exe",
    "windowsterminal.exe",
    "searchhost.exe",
    "shellexperiencehost.exe",
    "applicationframehost.exe",
    "lockapp.exe",
    "startmenuexperiencehost.exe",
    "rainmeter.exe",
    "yasb.exe",
    "windhawk.exe",
    "chrome.exe",
    "msedge.exe",
    "firefox.exe",
    "brave.exe",
    "opera.exe",
    "opera_gx.exe",
    "vivaldi.exe",
    "zen.exe",
    "tor.exe",
    "discord.exe",
    "spotify.exe",
    "telegram.exe",
    "slack.exe",
    "teams.exe",
    "zoom.exe",
    "obs64.exe",
    "obs32.exe",
    "vlc.exe",
    "monectserver.exe",
    "monectserverservice.exe",
    "pcremotereceiver.exe",
    "steam.exe",
    "steamwebhelper.exe",
    "epicgameslauncher.exe",
    "galaxyclient.exe",
    "ea.exe",
    "eadesktop.exe",
    "origin.exe",
    "battlenet.exe",
    "ubisoftconnect.exe",
    "upc.exe",
    "riotclientservices.exe",
    "xboxpcapp.exe",
    "gog.exe",
    "python.exe",
    "roomlights_capture.exe",
}


# ---------------------------------------------------------------------------
# Win32 Process Snapshot & Foreground Window Check
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


def _get_running_processes() -> Set[str]:
    """Capture a snapshot of all active process executable names (lowercase)."""
    running = set()
    TH32CS_SNAPPROCESS = 0x00000002
    kernel32 = ctypes.windll.kernel32
    h_snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if h_snapshot != -1 and h_snapshot != 0:
        pe = PROCESSENTRY32()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
        if kernel32.Process32First(h_snapshot, ctypes.byref(pe)):
            while True:
                exe = pe.szExeFile.decode("utf-8", errors="ignore").lower().strip()
                if exe:
                    running.add(exe)
                if not kernel32.Process32Next(h_snapshot, ctypes.byref(pe)):
                    break
        kernel32.CloseHandle(h_snapshot)
    return running


def is_sim_racing_running() -> bool:
    """
    Comprehensive multi-layer check for ANY sim racing game or launcher:
    1. Active telemetry stream (state.is_telemetry_active) or live RPM.
    2. Shared memory active session check (Assetto Corsa acpmf_physics, AMS2 $pcars2$).
    3. Process snapshot matching any sim racing game / launcher.
    4. Foreground window executable or window title matching sim racing keywords.
    """
    # 1. Telemetry stream check
    if state.is_telemetry_active(5.0) or state.rpm_pct > 0.0:
        return True

    # 2. Assetto Corsa Shared Memory check (instant detection even if telemetry reader is lagging)
    try:
        shm_ac = mmap.mmap(-1, 256, "acpmf_physics")
        shm_ac.seek(0)
        data = shm_ac.read(24)
        if len(data) >= 24:
            packet_id = int.from_bytes(data[0:4], byteorder="little", signed=True)
            rpms = int.from_bytes(data[20:24], byteorder="little", signed=True)
            shm_ac.close()
            if packet_id > 0 and rpms > 0:
                return True
        else:
            shm_ac.close()
    except Exception:
        pass

    # 3. Process snapshot check
    running_procs = _get_running_processes()
    if running_procs.intersection(_SIM_RACING_GAMES):
        return True

    # 4. Foreground window check (exe name + title heuristics)
    if PYWIN32_AVAILABLE:
        try:
            hwnd = win32gui.GetForegroundWindow()
            if hwnd:
                title = win32gui.GetWindowText(hwnd).strip().lower()
                if title:
                    for kw in _SIM_RACING_TITLE_KEYWORDS:
                        if kw in title:
                            return True

                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid:
                    kernel32 = ctypes.windll.kernel32
                    h_proc = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
                    if h_proc:
                        buf = (ctypes.c_char * 260)()
                        size = ctypes.wintypes.DWORD(260)
                        if kernel32.QueryFullProcessImageNameA(h_proc, 0, buf, ctypes.byref(size)):
                            exe = buf.value.decode("utf-8", errors="ignore").split("\\")[-1].lower()
                            kernel32.CloseHandle(h_proc)
                            if exe in _SIM_RACING_GAMES:
                                return True
                        else:
                            kernel32.CloseHandle(h_proc)
        except Exception:
            pass

    return False


def is_game_running() -> bool:
    """
    Check if any non-sim game is currently running:
    1. Returns False immediately if any sim racing game / launcher is running.
    2. Checks process snapshot against known games.
    3. Inspects foreground window process.
    """
    # Strict sim racing exclusion first
    if is_sim_racing_running():
        return False

    running_procs = _get_running_processes()

    # If any known game executable is running
    if running_procs.intersection(_KNOWN_GAMES):
        return True

    # Check foreground window
    if PYWIN32_AVAILABLE:
        try:
            hwnd = win32gui.GetForegroundWindow()
            if hwnd:
                title = win32gui.GetWindowText(hwnd).strip()
                if title:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    if pid:
                        kernel32 = ctypes.windll.kernel32
                        h_proc = kernel32.OpenProcess(0x1000, False, pid)
                        if h_proc:
                            buf = (ctypes.c_char * 260)()
                            size = ctypes.wintypes.DWORD(260)
                            if kernel32.QueryFullProcessImageNameA(h_proc, 0, buf, ctypes.byref(size)):
                                exe = buf.value.decode("utf-8", errors="ignore").split("\\")[-1].lower()
                                kernel32.CloseHandle(h_proc)
                                if exe and exe not in _IGNORED_PROCESSES and exe not in _SIM_RACING_GAMES:
                                    return True
                            else:
                                kernel32.CloseHandle(h_proc)
        except Exception:
            pass

    return False


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
        self._cb_ref = None
        self._is_active = False

    @property
    def is_active(self) -> bool:
        return self._is_active

    def start(self) -> bool:
        if self._is_active:
            return True
        if not VGAMEPAD_AVAILABLE:
            log.warning("vgamepad is not installed.")
            return False

        try:
            self._gamepad = vg.VDS4Gamepad()
            log.info("Virtual DS4 allocated on ViGEmBus.")
        except Exception as exc:
            log.warning("Could not attach virtual DS4: %s", exc)
            return False

        dll_path = _find_vigem_dll()
        if dll_path is None:
            log.warning("ViGEmClient.dll not found on system.")
            return False

        try:
            self._vigem = ctypes.CDLL(str(dll_path))

            # Set 64-bit argtypes and restype for ViGEm C API
            self._vigem.vigem_target_ds4_register_notification.argtypes = [
                c_void_p,
                c_void_p,
                DS4_NOTIFICATION_CB,
                c_void_p,
            ]
            self._vigem.vigem_target_ds4_register_notification.restype = c_ulong

            self._vigem.vigem_target_ds4_unregister_notification.argtypes = [c_void_p]
            self._vigem.vigem_target_ds4_unregister_notification.restype = None

            cb = DS4_NOTIFICATION_CB(self._on_ds4_notification)
            self._cb_ref = cb

            # In vgamepad, _busp is the client pointer and _devicep is the target device pointer
            bus_ptr = c_void_p(self._gamepad._busp)
            dev_ptr = c_void_p(self._gamepad._devicep)

            err = self._vigem.vigem_target_ds4_register_notification(
                bus_ptr, dev_ptr, cb, None
            )
            # 0x20000000 is VIGEM_ERROR_NONE (536870912)
            if err != 0x20000000 and err != 0:
                log.warning("vigem_target_ds4_register_notification returned: 0x%X", err)
                return False

            self._is_active = True
            log.info("Virtual DS4 connected & listening for PlayStation lightbar events.")
            return True
        except Exception as exc:
            log.warning("Failed to register DS4 notification: %s", exc)
            return False

    def stop(self) -> None:
        if not self._is_active:
            return
        self._is_active = False
        if self._vigem and self._gamepad:
            try:
                self._vigem.vigem_target_ds4_unregister_notification(
                    c_void_p(self._gamepad._devicep)
                )
            except Exception:
                pass
        self._gamepad = None
        self._cb_ref = None
        state.set_lightbar_rgb(0, 0, 0)
        try:
            from ipc_bridge import ipc_bridge
            ipc_bridge.clear()
        except Exception:
            pass
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
    - Automatically attaches when non-sim games start.
    - Strictly prevents attachment and immediately detaches during ANY sim racing game / launcher.
    - Auto-stops after confirmed game exit.
    """
    log.info("DS4 auto-detector running (auto-detects all games with sim racing protection)...")

    consecutive_inactive = 0
    INACTIVE_DEBOUNCE_TICKS = 4

    while not state.shutdown_event.is_set():
        # If any sim racing game / launcher / telemetry / shared memory is active, detach virtual DS4 immediately
        sim_racing_active = is_sim_racing_running()

        if sim_racing_active:
            if controller.is_active:
                log.info("Sim racing game detected -> detaching virtual DS4 immediately.")
                await asyncio.to_thread(controller.stop)
            state.set_lightbar_rgb(0, 0, 0)
            consecutive_inactive = INACTIVE_DEBOUNCE_TICKS
            await asyncio.sleep(0.5)
            continue

        game_active = await asyncio.to_thread(is_game_running)

        should_attach = config.ENABLE_VIRTUAL_DS4 or (config.ENABLE_VIRTUAL_DS4_AUTO and game_active)

        if should_attach:
            consecutive_inactive = 0
            if not controller.is_active:
                await asyncio.to_thread(controller.start)
        else:
            consecutive_inactive += 1
            if controller.is_active and consecutive_inactive >= INACTIVE_DEBOUNCE_TICKS:
                await asyncio.to_thread(controller.stop)

        await asyncio.sleep(1.5)

    await asyncio.to_thread(controller.stop)
    log.info("DS4 module stopped.")


