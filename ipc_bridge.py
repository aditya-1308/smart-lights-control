"""
ipc_bridge.py - Zero-latency Windows Shared Memory IPC Bridge for RoomLights.

Connects Python supervisor & integrations (F1, AMS2, Forza, DS4 games, CS2, Pomodoro)
to the high-speed 60 FPS C++ capture & UDP streaming engine (roomlights_capture.exe).
"""

import ctypes
import logging
import mmap
import struct
from typing import Optional, Sequence, Tuple

log = logging.getLogger("ipc_bridge")

# IPC constants
IPC_SHARED_MEM_NAME = "RoomLights_IPC"
IPC_MAGIC = 0x524C4950  # "RLIP"
IPC_VERSION = 1

# Modes:
# 0 = NONE / IDLE
# 1 = TELEMETRY_REV_METER (uses rpm_pct & is_limiter)
# 2 = DS4_LIGHTBAR (uses ds4_r, ds4_g, ds4_b)
# 3 = FULL_LIGHTBAR_ARRAY (uses seg1_rgb & seg2_rgb)
MODE_NONE = 0
MODE_REV_METER = 1
MODE_DS4_LIGHTBAR = 2
MODE_FULL_ARRAY = 3

# Struct layout:
#   uint32 magic (4)
#   uint32 version (4)
#   uint32 sequence (4)
#   uint8  lightbar_mode (1)
#   float  rpm_pct (4)
#   uint8  is_limiter (1)
#   uint8  ds4_r (1)
#   uint8  ds4_g (1)
#   uint8  ds4_b (1)
#   uint8  seg1_rgb[17 * 3 = 51] (51)
#   uint8  seg2_rgb[18 * 3 = 54] (54)
#   uint8  seg3_rgb[6 * 3 = 18]  (18)
#   uint8  seg0_override_active  (1)
#   uint8  seg0_override_r       (1)
#   uint8  seg0_override_g       (1)
#   uint8  seg0_override_b       (1)
# Total size = 4 + 4 + 4 + 1 + 4 + 1 + 1 + 1 + 1 + 51 + 54 + 18 + 1 + 1 + 1 + 1 = 148 bytes.

_HEADER_FORMAT = "<III BfBBBB"
_HEADER_SIZE = struct.calcsize(_HEADER_FORMAT)
IPC_TOTAL_SIZE = 148


class IPCBridgeWriter:
    """Manages the Windows Shared Memory block for C++ engine synchronization."""

    def __init__(self) -> None:
        self._mmap: Optional[mmap.mmap] = None
        self._sequence: int = 0
        self._init_shm()

    def _init_shm(self) -> bool:
        try:
            self._mmap = mmap.mmap(-1, IPC_TOTAL_SIZE, IPC_SHARED_MEM_NAME)
            # Write zeroed initial block
            self._mmap.seek(0)
            self._mmap.write(b"\x00" * IPC_TOTAL_SIZE)
            log.info("RoomLights Shared Memory IPC Bridge initialized (%d bytes).", IPC_TOTAL_SIZE)
            return True
        except Exception as exc:
            log.warning("Could not initialize IPC bridge: %s", exc)
            self._mmap = None
            return False

    def update_telemetry(self, rpm_pct: float, is_limiter: bool = False) -> None:
        """Send Sim Racing RPM telemetry to C++ engine."""
        if not self._mmap:
            if not self._init_shm():
                return
        self._sequence += 1
        try:
            self._mmap.seek(0)
            header = struct.pack(
                _HEADER_FORMAT,
                IPC_MAGIC,
                IPC_VERSION,
                self._sequence,
                MODE_REV_METER,
                max(0.0, min(1.0, float(rpm_pct))),
                1 if is_limiter else 0,
                0, 0, 0,
            )
            self._mmap.write(header)
        except Exception:
            pass

    def update_ds4_color(self, r: int, g: int, b: int) -> None:
        """Send PlayStation DualShock 4 controller lightbar color to C++ engine."""
        if not self._mmap:
            if not self._init_shm():
                return
        self._sequence += 1
        try:
            self._mmap.seek(0)
            header = struct.pack(
                _HEADER_FORMAT,
                IPC_MAGIC,
                IPC_VERSION,
                self._sequence,
                MODE_DS4_LIGHTBAR,
                0.0,
                0,
                max(0, min(255, int(r))),
                max(0, min(255, int(g))),
                max(0, min(255, int(b))),
            )
            self._mmap.write(header)
        except Exception:
            pass

    def update_raw_lightbar(
        self,
        seg1_colors: Sequence[Tuple[int, int, int]],
        seg2_colors: Sequence[Tuple[int, int, int]],
        seg3_colors: Optional[Sequence[Tuple[int, int, int]]] = None,
    ) -> None:
        """Send custom raw RGB arrays for Seg 1 (17 LEDs), Seg 2 (18 LEDs), and Seg 3 (6 LEDs)."""
        if not self._mmap:
            if not self._init_shm():
                return
        self._sequence += 1
        try:
            self._mmap.seek(0)
            header = struct.pack(
                _HEADER_FORMAT,
                IPC_MAGIC,
                IPC_VERSION,
                self._sequence,
                MODE_FULL_ARRAY,
                0.0,
                0,
                0, 0, 0,
            )
            self._mmap.write(header)

            # Pack Seg 1 (17 LEDs * 3 = 51 bytes)
            s1_bytes = bytearray(51)
            for i, (cr, cg, cb) in enumerate(seg1_colors[:17]):
                s1_bytes[i * 3]     = max(0, min(255, cr))
                s1_bytes[i * 3 + 1] = max(0, min(255, cg))
                s1_bytes[i * 3 + 2] = max(0, min(255, cb))
            self._mmap.write(s1_bytes)

            # Pack Seg 2 (18 LEDs * 3 = 54 bytes)
            s2_bytes = bytearray(54)
            for i, (cr, cg, cb) in enumerate(seg2_colors[:18]):
                s2_bytes[i * 3]     = max(0, min(255, cr))
                s2_bytes[i * 3 + 1] = max(0, min(255, cg))
                s2_bytes[i * 3 + 2] = max(0, min(255, cb))
            self._mmap.write(s2_bytes)

            # Pack Seg 3 (6 LEDs * 3 = 18 bytes) if provided
            if seg3_colors:
                s3_bytes = bytearray(18)
                for i, (cr, cg, cb) in enumerate(seg3_colors[:6]):
                    s3_bytes[i * 3]     = max(0, min(255, cr))
                    s3_bytes[i * 3 + 1] = max(0, min(255, cg))
                    s3_bytes[i * 3 + 2] = max(0, min(255, cb))
                self._mmap.write(s3_bytes)
        except Exception:
            pass

    def update_seg0_override(self, r: int, g: int, b: int) -> None:
        """Override Segment 0 with a solid color (e.g. Chroma / CS2 flashbang)."""
        if not self._mmap:
            if not self._init_shm():
                return
        self._sequence += 1
        try:
            self._mmap.seek(144)  # offset of seg0_override_active
            self._mmap.write(bytes([1, max(0, min(255, int(r))), max(0, min(255, int(g))), max(0, min(255, int(b)))]))
        except Exception:
            pass

    def clear_seg0_override(self) -> None:
        """Clear Segment 0 override so it returns to 60 FPS DXGI screen capture."""
        if not self._mmap:
            return
        self._sequence += 1
        try:
            self._mmap.seek(144)  # offset of seg0_override_active
            self._mmap.write(b"\x00\x00\x00\x00")
        except Exception:
            pass

    def clear(self) -> None:
        """Clear active mode so C++ engine returns to default ambient behavior."""
        if not self._mmap:
            return
        self._sequence += 1
        try:
            self._mmap.seek(0)
            header = struct.pack(
                _HEADER_FORMAT,
                IPC_MAGIC,
                IPC_VERSION,
                self._sequence,
                MODE_NONE,
                0.0,
                0,
                0, 0, 0,
            )
            self._mmap.write(header)
        except Exception:
            pass


# Global singleton instance
ipc_bridge = IPCBridgeWriter()
