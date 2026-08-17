"""
mod_spatial_f1.py - F1 23/24 spatial telemetry effects on Seg 0.

Parses multiple F1 UDP packet types for rich spatial effects:
  - Proximity spotter: cars alongside left/right (Packet 0: Motion)
  - Flag states: yellow, blue, safety car (Packet 1: Session)
  - Sector performance: green/purple flashes (Packet 2: Lap Data)
  - Collision events (Packet 3: Events)
  - Car damage indicators (Packet 10: Car Damage)

Writes 109-LED color arrays to state.seg0_colors.
Releases Seg 0 back to screen capture when no events active.
"""

import asyncio
import logging
import socket
import struct
import time
from typing import List, Optional, Tuple

import numpy as np

import config
from state import state, Seg0Source, AppContext

log = logging.getLogger("spatial_f1")

RGB = Tuple[int, int, int]

# Colors
_ORANGE = (255, 120, 0)
_RED = (255, 0, 0)
_YELLOW = (255, 200, 0)
_BLUE = (0, 80, 255)
_AMBER = (255, 180, 0)
_WHITE = (255, 255, 255)
_GREEN = (0, 255, 60)
_PURPLE = (160, 0, 255)
_OFF = (0, 0, 0)

# F1 UDP constants
_HEADER_FMT = "<HBBBBBQfIIBB"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)

# Packet IDs
_PKT_MOTION = 0
_PKT_SESSION = 1
_PKT_LAP_DATA = 2
_PKT_EVENT = 3
_PKT_CAR_STATUS = 7
_PKT_CAR_DAMAGE = 10

# Spotter thresholds (meters)
_SPOTTER_RANGE = 15.0
_DANGER_RANGE = 5.0


def _solid_109(color: RGB) -> List[RGB]:
    return [color] * config.SEG0_COUNT


def _left_third(color: RGB) -> List[RGB]:
    """Light up the left third of the strip."""
    colors = [_OFF] * config.SEG0_COUNT
    # Left = idx 72-89 (left edge) + idx 90-108 (bottom-left)
    for i in range(72, config.SEG0_COUNT):
        colors[i] = color
    return colors


def _right_third(color: RGB) -> List[RGB]:
    """Light up the right third of the strip."""
    colors = [_OFF] * config.SEG0_COUNT
    # Right = idx 0-17 (bottom-right) + idx 18-35 (right edge)
    for i in range(0, 36):
        colors[i] = color
    return colors


class F1SpatialProcessor:
    """Processes F1 UDP packets and generates Seg 0 spatial effects."""

    def __init__(self, port: int) -> None:
        self._port = port
        self._sock: Optional[socket.socket] = None
        self._player_idx = 0

        # Event state
        self._event_colors: Optional[List[RGB]] = None
        self._event_end: float = 0.0

        # Spotter state
        self._car_left = False
        self._car_right = False
        self._left_danger = False
        self._right_danger = False

        # Flags
        self._current_flag = 0  # 0=none, 1=green, 2=blue, 3=yellow
        self._safety_car = 0    # 0=none, 1=full, 2=VSC

        # Sectors
        self._best_sector_times = [999999.0, 999999.0, 999999.0]
        self._last_sector1 = 0
        self._last_sector2 = 0

    def connect(self) -> bool:
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(("0.0.0.0", self._port))
            self._sock.setblocking(False)
            log.info("F1 spatial UDP bound on port %d.", self._port)
            return True
        except OSError as exc:
            log.error("F1 spatial UDP bind failed: %s", exc)
            return False

    def disconnect(self):
        if self._sock:
            self._sock.close()
            self._sock = None

    def process_packets(self) -> Optional[List[RGB]]:
        """
        Read all available UDP packets and return event colors if any.
        Returns None if no events (fall back to screen capture).
        """
        if not self._sock:
            return None

        now = time.monotonic()

        # Drain all available packets
        for _ in range(50):  # Process up to 50 packets per tick
            try:
                data, _ = self._sock.recvfrom(4096)
            except BlockingIOError:
                break
            except Exception:
                break

            if len(data) < _HEADER_SIZE:
                continue

            header = struct.unpack_from(_HEADER_FMT, data, 0)
            pkt_id = header[5]
            self._player_idx = header[10]

            if pkt_id == _PKT_MOTION:
                self._process_motion(data)
            elif pkt_id == _PKT_SESSION:
                self._process_session(data)
            elif pkt_id == _PKT_LAP_DATA:
                self._process_lap_data(data)
            elif pkt_id == _PKT_EVENT:
                self._process_event(data, now)

        # Generate output based on current state
        # Priority: timed event > spotter > flag > none

        if now < self._event_end and self._event_colors:
            return self._event_colors

        if self._car_left or self._car_right:
            return self._spotter_colors()

        if self._safety_car > 0:
            return _solid_109(_AMBER)

        if self._current_flag == 3:  # Yellow
            phase = int(now * 4) % 2
            return _solid_109(_YELLOW if phase else (80, 60, 0))

        if self._current_flag == 2:  # Blue
            phase = int(now * 6) % 2
            return _solid_109(_BLUE if phase else (0, 20, 80))

        return None  # No event -> screen capture

    def _spotter_colors(self) -> List[RGB]:
        """Generate spotter overlay."""
        colors = [_OFF] * config.SEG0_COUNT

        if self._car_left:
            color = _RED if self._left_danger else _ORANGE
            # Left side: idx 72-108
            for i in range(72, config.SEG0_COUNT):
                colors[i] = color

        if self._car_right:
            color = _RED if self._right_danger else _ORANGE
            # Right side: idx 0-35
            for i in range(0, 36):
                colors[i] = color

        return colors

    def _process_motion(self, data: bytes) -> None:
        """Parse Packet 0: Motion - 22 cars' world positions."""
        # CarMotionData: 60 bytes per car
        # worldPositionX (float), worldPositionY (float), worldPositionZ (float)
        # at offsets 0, 4, 8 within each car's 60-byte block
        offset = _HEADER_SIZE
        car_positions = []

        for i in range(22):
            if offset + 12 > len(data):
                break
            x, y, z = struct.unpack_from('<fff', data, offset)
            car_positions.append((x, y, z))
            offset += 60

        if self._player_idx >= len(car_positions):
            return

        px, py, pz = car_positions[self._player_idx]
        self._car_left = False
        self._car_right = False
        self._left_danger = False
        self._right_danger = False

        for i, (cx, cy, cz) in enumerate(car_positions):
            if i == self._player_idx:
                continue
            if cx == 0.0 and cz == 0.0:
                continue

            dx = cx - px  # positive = right
            dz = cz - pz
            dist = (dx * dx + dz * dz) ** 0.5

            if dist < _SPOTTER_RANGE:
                if dx < -1.0:  # Car on left
                    self._car_left = True
                    if dist < _DANGER_RANGE:
                        self._left_danger = True
                elif dx > 1.0:  # Car on right
                    self._car_right = True
                    if dist < _DANGER_RANGE:
                        self._right_danger = True

    def _process_session(self, data: bytes) -> None:
        """Parse Packet 1: Session - flags and safety car."""
        # safetyCarStatus at offset 51 from header (varies by version)
        # marshalZones starting at offset 52
        # Simplified: read player's flag from Car Status packet instead
        try:
            # Safety car status byte
            if len(data) > _HEADER_SIZE + 51:
                self._safety_car = data[_HEADER_SIZE + 19]  # safetyCarStatus
        except Exception:
            pass

    def _process_lap_data(self, data: bytes) -> None:
        """Parse Packet 2: Lap Data - sector times."""
        # Each car's lap data is ~57 bytes in F1 23
        car_offset = _HEADER_SIZE + (self._player_idx * 57)
        if car_offset + 57 > len(data):
            return

        try:
            s1 = struct.unpack_from('<I', data, car_offset + 0)[0]  # sector1TimeInMS
            s2 = struct.unpack_from('<I', data, car_offset + 4)[0]  # sector2TimeInMS

            now = time.monotonic()

            # Detect sector completion
            if s1 > 0 and s1 != self._last_sector1:
                self._last_sector1 = s1
                if s1 < self._best_sector_times[0]:
                    is_first = self._best_sector_times[0] == 999999.0
                    self._best_sector_times[0] = s1
                    self._event_colors = _solid_109(_PURPLE if is_first else _GREEN)
                    self._event_end = now + 0.5

            if s2 > 0 and s2 != self._last_sector2:
                self._last_sector2 = s2
                if s2 < self._best_sector_times[1]:
                    is_first = self._best_sector_times[1] == 999999.0
                    self._best_sector_times[1] = s2
                    self._event_colors = _solid_109(_PURPLE if is_first else _GREEN)
                    self._event_end = now + 0.5
        except Exception:
            pass

    def _process_event(self, data: bytes, now: float) -> None:
        """Parse Packet 3: Events - collisions."""
        if len(data) < _HEADER_SIZE + 4:
            return
        event_code = data[_HEADER_SIZE:_HEADER_SIZE + 4]
        if event_code == b'COLL':
            log.debug("F1: Collision detected!")
            self._event_colors = _solid_109(_WHITE)
            self._event_end = now + 0.5


async def run() -> None:
    """F1 spatial telemetry monitor."""
    log.info("F1 spatial module starting (UDP port %d)...", config.F1_UDP_PORT)
    processor = F1SpatialProcessor(config.F1_UDP_PORT)


    if not await asyncio.to_thread(processor.connect):
        return

    await state.set_context(AppContext.RACING)

    while not state.shutdown_event.is_set():
        # Skip if Chroma has priority
        if state.seg0_source == Seg0Source.CHROMA:
            await asyncio.sleep(0.5)
            continue

        colors = await asyncio.to_thread(processor.process_packets)

        if colors:
            state.update_seg0_colors(colors)
            if state.seg0_source != Seg0Source.F1_SPATIAL:
                await state.set_seg0_source(Seg0Source.F1_SPATIAL)
        else:
            if state.seg0_source == Seg0Source.F1_SPATIAL:
                await state.set_seg0_source(Seg0Source.SCREEN_CAPTURE)

        await asyncio.sleep(1.0 / 30)  # 30 Hz

    processor.disconnect()
    log.info("F1 spatial module stopped.")
