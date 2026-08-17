"""
mod_spatial_ac.py — Assetto Corsa spatial telemetry effects on Seg 0.

Reads acpmf_graphics and acpmf_physics shared memory for:
  - Flag states (yellow, blue, black, checkered, white)
  - Track limits (numberOfTyresOut: 0-4)
  - Sector performance (green/purple sector flashes)
  - Pit lane status

Writes 109-LED color arrays to state.seg0_colors when events occur.
Falls back to screen capture during normal racing (no events).

Limitation: Vanilla AC1 shared memory does NOT expose other car positions.
Proximity spotter requires CSP extended memory (future enhancement).
"""

import asyncio
import ctypes
import logging
import mmap
import time
from ctypes import Structure, c_float, c_int32, c_wchar
from typing import List, Optional, Tuple

import config
from state import state, Seg0Source, AppContext

log = logging.getLogger("spatial_ac")

RGB = Tuple[int, int, int]

# Colors
_YELLOW = (255, 200, 0)
_BLUE = (0, 80, 255)
_BLACK_RED = (80, 0, 0)
_WHITE = (255, 255, 255)
_GREEN = (0, 255, 60)
_PURPLE = (160, 0, 255)
_ORANGE = (255, 120, 0)
_RED = (255, 0, 0)
_PIT_BLUE = (60, 80, 120)
_OFF = (0, 0, 0)

# AC Flag enum values
_AC_NO_FLAG = 0
_AC_BLUE_FLAG = 1
_AC_YELLOW_FLAG = 2
_AC_BLACK_FLAG = 3
_AC_WHITE_FLAG = 4
_AC_CHECKERED_FLAG = 5
_AC_PENALTY_FLAG = 6

# AC Status enum
_AC_STATUS_LIVE = 2


class SPageFileGraphics(Structure):
    _pack_ = 4
    _fields_ = [
        ('packetId', c_int32),
        ('status', c_int32),
        ('session', c_int32),
        ('currentTime', c_wchar * 15),
        ('lastTime', c_wchar * 15),
        ('bestTime', c_wchar * 15),
        ('split', c_wchar * 15),
        ('completedLaps', c_int32),
        ('position', c_int32),
        ('iCurrentTime', c_int32),
        ('iLastTime', c_int32),
        ('iBestTime', c_int32),
        ('sessionTimeLeft', c_float),
        ('distanceTraveled', c_float),
        ('isInPit', c_int32),
        ('currentSectorIndex', c_int32),
        ('lastSectorTime', c_int32),
        ('numberOfLaps', c_int32),
        ('tyreCompound', c_wchar * 33),
        ('replayTimeMultiplier', c_float),
        ('normalizedCarPosition', c_float),
        ('carCoordinates', c_float * 3),
        ('penaltyTime', c_float),
        ('flag', c_int32),
        ('idealLineOn', c_int32),
        ('isInPitLane', c_int32),
        ('surfaceGrip', c_float),
        ('mandatoryPitDone', c_int32),
        ('windSpeed', c_float),
        ('windDirection', c_float),
    ]


def _solid_109(color: RGB) -> List[RGB]:
    """Return a solid 109-LED color array."""
    return [color] * config.SEG0_COUNT


def _checkered_pattern(phase: bool) -> List[RGB]:
    """Alternating black/white blocks (8 LEDs per block)."""
    colors = []
    block_size = 8
    for i in range(config.SEG0_COUNT):
        block_idx = (i // block_size) % 2
        if phase:
            block_idx = 1 - block_idx
        colors.append(_WHITE if block_idx == 0 else _OFF)
    return colors


def _flash_strip(color: RGB, progress: float) -> List[RGB]:
    """Sweep color from both ends toward center."""
    colors = [_OFF] * config.SEG0_COUNT
    n_lit = int(progress * config.SEG0_COUNT / 2)
    for i in range(min(n_lit, config.SEG0_COUNT)):
        colors[i] = color
        idx = config.SEG0_COUNT - 1 - i
        if idx >= 0:
            colors[idx] = color
    return colors


def _track_limit_flash(tyres_out: int) -> List[RGB]:
    """Flash based on how many wheels are off track."""
    if tyres_out >= 4:
        return _solid_109(_RED)
    elif tyres_out >= 2:
        return _solid_109(_ORANGE)
    elif tyres_out == 1:
        # Subtle orange tint
        return [(40, 20, 0)] * config.SEG0_COUNT
    return _solid_109(_OFF)


async def run() -> None:
    """AC spatial telemetry monitor."""
    log.info("AC spatial module starting.")

    graphics_shm = None
    connected = False
    last_sector_idx = -1
    last_sector_time = 0
    best_sector_times = [999999, 999999, 999999]
    event_end_time = 0.0
    last_flag = _AC_NO_FLAG
    checkered_phase = False
    last_check = 0.0

    while not state.shutdown_event.is_set():
        now = time.monotonic()

        # Try to connect to AC shared memory
        if not connected:
            try:
                graphics_shm = mmap.mmap(
                    -1,
                    ctypes.sizeof(SPageFileGraphics),
                    "Local\\acpmf_graphics",
                    access=mmap.ACCESS_READ,
                )
                connected = True
                await state.set_seg0_source(Seg0Source.AC_SPATIAL)
                await state.set_context(AppContext.RACING)
                log.info("AC shared memory (graphics) opened.")
            except OSError:
                await asyncio.sleep(3.0)
                continue

        # Read graphics page
        try:
            graphics_shm.seek(0)
            gfx = SPageFileGraphics.from_buffer_copy(graphics_shm)
        except Exception:
            connected = False
            if graphics_shm:
                graphics_shm.close()
            await state.set_seg0_source(Seg0Source.SCREEN_CAPTURE)
            await asyncio.sleep(3.0)
            continue

        # Not in live session -> release Seg 0
        if gfx.status != _AC_STATUS_LIVE:
            if state.seg0_source == Seg0Source.AC_SPATIAL:
                await state.set_seg0_source(Seg0Source.SCREEN_CAPTURE)
            await asyncio.sleep(1.0)
            continue

        # Also read physics for track limits
        tyres_out = 0
        try:
            physics_shm = mmap.mmap(-1, 512, "Local\\acpmf_physics", access=mmap.ACCESS_READ)
            physics_shm.seek(0)
            # numberOfTyresOut is at a known offset in SPageFilePhysics
            # After all the float arrays, it's at byte offset ~232
            # We already defined the struct in mod_a_simracing.py
            # Quick read: rpms is at offset 16 (4 ints + 1 float = 4*4+4=20.. actually offset 16)
            # numberOfTyresOut: skip to correct position. 
            # Simpler: import the struct from mod_a_simracing
            from mod_a_simracing import SPageFilePhysics
            physics_shm.seek(0)
            phys = SPageFilePhysics.from_buffer_copy(physics_shm)
            tyres_out = phys.numberOfTyresOut
            physics_shm.close()
        except Exception:
            pass

        event_colors = None

        # --- Flag events ---
        flag = gfx.flag

        if flag == _AC_YELLOW_FLAG:
            # Pulse yellow at 2 Hz
            phase = int(now * 4) % 2
            event_colors = _solid_109(_YELLOW if phase else (80, 60, 0))

        elif flag == _AC_BLUE_FLAG:
            # Pulse blue at 3 Hz
            phase = int(now * 6) % 2
            event_colors = _solid_109(_BLUE if phase else (0, 20, 80))

        elif flag == _AC_BLACK_FLAG:
            event_colors = _solid_109(_BLACK_RED)

        elif flag == _AC_CHECKERED_FLAG:
            checkered_phase = not checkered_phase
            event_colors = _checkered_pattern(checkered_phase)

        elif flag == _AC_WHITE_FLAG:
            event_colors = _solid_109(_WHITE)

        # --- Track limits ---
        elif tyres_out >= 1:
            event_colors = _track_limit_flash(tyres_out)
            event_end_time = now + 0.5  # Show for 500ms

        # --- Sector performance ---
        elif gfx.currentSectorIndex != last_sector_idx and last_sector_idx >= 0:
            sector_time = gfx.lastSectorTime
            if sector_time > 0:
                sector_idx = last_sector_idx
                if sector_time < best_sector_times[sector_idx]:
                    # Check if it's session best (first ever = purple)
                    if best_sector_times[sector_idx] == 999999:
                        event_colors = _solid_109(_PURPLE)
                    else:
                        event_colors = _solid_109(_GREEN)
                    best_sector_times[sector_idx] = sector_time
                    event_end_time = now + 0.5

        last_sector_idx = gfx.currentSectorIndex

        # --- Apply event or release ---
        if event_colors:
            state.update_seg0_colors(event_colors)
            if state.seg0_source != Seg0Source.AC_SPATIAL:
                await state.set_seg0_source(Seg0Source.AC_SPATIAL)
        elif now < event_end_time:
            pass  # Keep showing current event
        else:
            # No event active: release to screen capture
            if state.seg0_source == Seg0Source.AC_SPATIAL and flag == _AC_NO_FLAG:
                await state.set_seg0_source(Seg0Source.SCREEN_CAPTURE)

        await asyncio.sleep(1.0 / 30)  # 30 Hz polling

    # Cleanup
    if graphics_shm:
        try:
            graphics_shm.close()
        except Exception:
            pass
    log.info("AC spatial module stopped.")
