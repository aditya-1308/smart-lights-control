"""
wled_udp.py - WLED UDP Realtime protocol for low-latency LED updates.

WLED supports a simple UDP realtime protocol on port 21324 (WLED_REALTIME_UDP_PORT).
Packet format (WARLS / WLED Realtime):
  Byte 0: Protocol  (0x01 = WARLS, 0x02 = DRGB, 0x04 = DNRGB)
  Byte 1: Timeout   (seconds WLED holds realtime mode after last packet)
  Bytes 2+: R, G, B per LED (DRGB mode - all LEDs from index 0)

This is orders of magnitude faster than the HTTP JSON API for per-frame
LED updates. HTTP has ~50-200ms round trip; UDP has <1ms.

Used for Seg 0 (109-LED screen ambient strip) where per-frame updates matter.
The HTTP API is still used for control commands (segment config, effects, etc.).
"""

import asyncio
import logging
import socket
from typing import List, Tuple

import config

log = logging.getLogger("wled_udp")

RGB = Tuple[int, int, int]

# WLED UDP realtime port (fixed in WLED firmware)
WLED_UDP_PORT = 21324

# Protocol byte: DRGB = 0x02 - sets all LEDs from index 0 sequentially
_PROTO_DRGB = 0x02

# Timeout: how long WLED holds realtime mode after the last packet (seconds).
# Set high enough that a single missed frame doesn't flash to the stored effect.
_REALTIME_TIMEOUT = 2


class WLEDUDPClient:
    """
    Sends LED color arrays to WLED over UDP using the DRGB realtime protocol.

    One socket is shared for all sends. Non-blocking and fire-and-forget.
    """

    def __init__(self, ip: str, port: int = WLED_UDP_PORT) -> None:
        self._ip = ip
        self._port = port
        self._sock: socket.socket | None = None
        # Physical LED start offsets for each segment
        self._seg0_start: int = 0
        self._seg1_start: int = 0
        self._seg2_start: int = 0

    def start(self, seg0_start: int = 0,
              seg1_start: int = 0, seg2_start: int = 0) -> None:
        """Open the UDP socket."""
        self._seg0_start = seg0_start
        self._seg1_start = seg1_start
        self._seg2_start = seg2_start
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)
        log.info("WLED UDP client ready → %s:%d "
                 "(Seg0@%d Seg1@%d Seg2@%d)",
                 self._ip, self._port,
                 seg0_start, seg1_start, seg2_start)

    def stop(self) -> None:
        """Close the UDP socket."""
        if self._sock:
            self._sock.close()
            self._sock = None

    def send_seg0(self, colors: List[RGB]) -> None:
        """
        Send 109 LED colors for Seg 0 over UDP realtime (DNRGB protocol).

        DNRGB allows specifying a start index so we only address our segment's
        LEDs without touching others.

        Packet format (DNRGB = 0x04):
          Byte 0: 0x04 (DNRGB)
          Byte 1: timeout
          Byte 2: start index high byte
          Byte 3: start index low byte
          Bytes 4+: R, G, B, R, G, B, ...
        """
        if not self._sock or not colors:
            return

        start = self._seg0_start
        # DNRGB header: protocol=0x04, timeout, start_high, start_low
        header = bytes([0x04, _REALTIME_TIMEOUT,
                        (start >> 8) & 0xFF, start & 0xFF])
        # Flatten RGB tuples into bytes
        body = bytes([channel for r, g, b in colors for channel in (r, g, b)])
        packet = header + body

        try:
            self._sock.sendto(packet, (self._ip, self._port))
        except BlockingIOError:
            pass  # Socket buffer full - drop frame, not worth retrying
        except OSError:
            pass


    def _send_dnrgb(self, start: int, colors: List[RGB]) -> None:
        """Internal: send a DNRGB packet for any segment start offset."""
        if not self._sock or not colors:
            return
        header = bytes([0x04, _REALTIME_TIMEOUT,
                        (start >> 8) & 0xFF, start & 0xFF])
        body = bytes([ch for r, g, b in colors for ch in (r, g, b)])
        try:
            self._sock.sendto(header + body, (self._ip, self._port))
        except (BlockingIOError, OSError):
            pass

    def send_lightbar(self, seg1_colors: List[RGB], seg2_colors: List[RGB]) -> None:
        """
        Send lightbar colors for Seg 1 and Seg 2 over UDP.

        seg2_colors: left half (physical order)
        seg1_colors: right half (physical order, already reversed by caller)
        """
        self._send_dnrgb(self._seg2_start, seg2_colors)
        if self._seg1_start != self._seg2_start:
            self._send_dnrgb(self._seg1_start, seg1_colors)


# Module-level singleton - import and use directly
udp_client = WLEDUDPClient(config.WLED_IP)
