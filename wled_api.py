"""
wled_api.py - Async HTTP wrapper for the WLED JSON API.

Features:
  - Persistent ``aiohttp.ClientSession`` with connection pooling.
  - Silently catches and ignores timeouts / connection errors.
  - Helper to build the ``"i"`` (individual LED) array for per-LED coloring.
  - Helper to split a 35-element logical lightbar into Seg 1 + Seg 2 payloads,
    accounting for Seg 1's reversed physical wiring direction.
"""

import asyncio
import logging
from typing import List, Optional, Tuple

import aiohttp

import config

log = logging.getLogger("wled_api")

# Type alias: an RGB color tuple
RGB = Tuple[int, int, int]


class WLEDClient:
    """
    Async HTTP client for WLED's ``/json/state`` endpoint.

    Usage::

        async with WLEDClient() as wled:
            await wled.set_solid(seg_id=1, rgb=(255, 0, 0))
            await wled.set_lightbar(color_array)
    """

    def __init__(self) -> None:
        self._session: Optional[aiohttp.ClientSession] = None
        self._timeout = aiohttp.ClientTimeout(total=config.WLED_TIMEOUT)
        # Rate-limit: minimum interval between requests (seconds)
        self._min_interval = 1.0 / config.LIGHTBAR_UPDATE_HZ
        self._last_send_time: float = 0.0

    async def __aenter__(self) -> "WLEDClient":
        await self.start()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def start(self) -> None:
        """Create the persistent HTTP session."""
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(limit=2, keepalive_timeout=30)
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=self._timeout,
            )

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()

    # -----------------------------------------------------------------
    # Low-level send
    # -----------------------------------------------------------------
    async def send_state(self, payload: dict) -> bool:
        """
        POST a partial JSON state update to WLED.

        Returns True on success, False on any error (silently swallowed).
        """
        if self._session is None or self._session.closed:
            await self.start()

        try:
            async with self._session.post(
                config.WLED_STATE_URL,
                json=payload,
            ) as resp:
                if resp.status == 200:
                    return True
                log.warning("WLED responded %d", resp.status)
                return False
        except asyncio.TimeoutError:
            # Expected when WLED is busy - silently drop
            return False
        except aiohttp.ClientError as exc:
            log.debug("WLED connection error: %s", exc)
            return False
        except Exception as exc:
            log.debug("WLED unexpected error: %s", exc)
            return False

    async def fetch_segment_info(self) -> dict:
        """
        Query WLED's current segment configuration dynamically over HTTP.

        Returns dict mapping seg_id -> {"len": int, "start": int, "stop": int, "rev": bool, "on": bool}.
        Returns empty dict if WLED is unreachable.
        """
        if self._session is None or self._session.closed:
            await self.start()

        try:
            async with self._session.get(config.WLED_STATE_URL) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # WLED returns state wrapped in state object or root
                    state_obj = data.get("state", data)
                    segs = state_obj.get("seg", [])
                    result = {}
                    for s in segs:
                        seg_id = s.get("id", 0)
                        start = s.get("start", 0)
                        stop = s.get("stop", 0)
                        length = s.get("len", max(0, stop - start))
                        rev = s.get("rev", False)
                        on = s.get("on", True)
                        result[seg_id] = {
                            "len": length,
                            "start": start,
                            "stop": stop,
                            "rev": rev,
                            "on": on,
                        }
                    if result:
                        log.info("Auto-discovered %d segments from WLED at %s:",
                                 len(result), config.WLED_IP)
                        for sid, sinfo in result.items():
                            log.info("  Seg %d: %d LEDs (idx %d..%d)%s",
                                     sid, sinfo["len"], sinfo["start"], sinfo["stop"],
                                     " [REVERSED]" if sinfo["rev"] else "")
                    return result
                return {}
        except Exception as exc:
            log.warning("Could not auto-discover WLED segments (%s). Using config defaults.", exc)
            return {}


    # -----------------------------------------------------------------
    # High-level helpers
    # -----------------------------------------------------------------
    async def set_solid(self, seg_id: int, rgb: RGB, brightness: int = 255) -> bool:
        """Set a segment to a solid color (effect = 0 / Solid)."""
        return await self.send_state({
            "seg": [{
                "id": seg_id,
                "on": True,
                "bri": brightness,
                "fx": 0,
                "col": [list(rgb)],
            }]
        })

    async def set_segment_off(self, seg_id: int) -> bool:
        """Turn a segment off."""
        return await self.send_state({
            "seg": [{"id": seg_id, "on": False}]
        })

    async def set_individual_leds(self, seg_id: int, i_array: list) -> bool:
        """
        Set individual LEDs within a segment using WLED's ``"i"`` array.

        ``i_array`` format: ``[start, stop, [R,G,B], start, stop, [R,G,B], ...]``
        where ``stop`` is exclusive and indices are segment-relative.
        """
        return await self.send_state({
            "seg": [{
                "id": seg_id,
                "on": True,
                "fx": 0,    # Solid - "i" array overrides per-LED
                "i": i_array,
            }]
        })

    async def set_effect(
        self,
        seg_id: int,
        fx: int,
        ix: int = 128,
        sx: int = 128,
        col: Optional[RGB] = None,
        brightness: int = 255,
    ) -> bool:
        """
        Set a built-in WLED effect on a segment.

        Args:
            seg_id: WLED segment ID.
            fx: Effect ID (e.g. 98 = Percent).
            ix: Effect intensity (0–255).
            sx: Effect speed (0–255).
            col: Primary color as (R, G, B).
            brightness: Segment brightness (0–255).
        """
        seg: dict = {
            "id": seg_id,
            "on": True,
            "bri": brightness,
            "fx": fx,
            "ix": ix,
            "sx": sx,
        }
        if col is not None:
            seg["col"] = [list(col)]
        return await self.send_state({"seg": [seg]})

    # -----------------------------------------------------------------
    # Lightbar helpers (35-LED unified bar → Seg 1 + Seg 2)
    # -----------------------------------------------------------------
    async def set_lightbar(self, color_array: List[RGB]) -> bool:
        """
        Send a 35-element color array to the unified lightbar.

        Supports both 2-segment lightbars (Seg 1 + Seg 2) and single-segment lightbars.
        """
        assert len(color_array) == config.LIGHTBAR_TOTAL, \
            f"Expected {config.LIGHTBAR_TOTAL} colors, got {len(color_array)}"

        # If both lightbar halves point to the same segment ID (single-segment bar)
        if config.SEG1_ID == config.SEG2_ID:
            i_array = _build_per_led_i_array(color_array)
            return await self.send_state({
                "seg": [{"id": config.SEG1_ID, "on": True, "fx": 0, "i": i_array}]
            })

        # Dual-segment lightbar: Seg 1 = left half (0..17), Seg 2 = right half (18..35)
        seg1_colors = color_array[:config.SEG1_COUNT]
        seg2_colors = color_array[config.SEG1_COUNT:]

        seg1_i = _build_per_led_i_array(seg1_colors)
        seg2_i = _build_per_led_i_array(seg2_colors)

        return await self.send_state({
            "seg": [
                {"id": config.SEG1_ID, "on": True, "fx": 0, "i": seg1_i},
                {"id": config.SEG2_ID, "on": True, "fx": 0, "i": seg2_i},
            ]
        })

    async def set_lightbar_solid(self, rgb: RGB) -> bool:
        """Set lightbar segments to a single solid color."""
        if config.SEG1_ID == config.SEG2_ID:
            return await self.send_state({
                "seg": [{"id": config.SEG1_ID, "on": True, "fx": 0, "col": [list(rgb)]}]
            })
        return await self.send_state({
            "seg": [
                {"id": config.SEG1_ID, "on": True, "fx": 0, "col": [list(rgb)]},
                {"id": config.SEG2_ID, "on": True, "fx": 0, "col": [list(rgb)]},
            ]
        })

    async def set_lightbar_off(self) -> bool:
        """Turn off lightbar segments."""
        if config.SEG1_ID == config.SEG2_ID:
            return await self.send_state({
                "seg": [{"id": config.SEG1_ID, "on": False}]
            })
        return await self.send_state({
            "seg": [
                {"id": config.SEG1_ID, "on": False},
                {"id": config.SEG2_ID, "on": False},
            ]
        })


    # -----------------------------------------------------------------
    # Seg 0 helpers (109-LED screen ambient / spatial effects)
    # -----------------------------------------------------------------
    async def set_seg0(self, color_array: List[RGB]) -> bool:
        """
        Send a 109-element color array to Seg 0.

        The array maps directly to LED indices 0-108 (clockwise from
        bottom-middle: bottom-right, right, top, left, bottom-left).
        """
        assert len(color_array) == config.SEG0_COUNT, \
            f"Expected {config.SEG0_COUNT} colors, got {len(color_array)}"

        i_array = _build_per_led_i_array(color_array)
        return await self.send_state({
            "seg": [{
                "id": config.SEG0_ID,
                "on": True,
                "fx": 0,
                "i": i_array,
            }]
        })

    async def set_seg0_solid(self, rgb: RGB) -> bool:
        """Set all 109 LEDs of Seg 0 to a single color."""
        return await self.send_state({
            "seg": [{
                "id": config.SEG0_ID,
                "on": True,
                "fx": 0,
                "col": [list(rgb)],
            }]
        })

    async def set_seg0_off(self) -> bool:
        """Turn Seg 0 off."""
        return await self.send_state({
            "seg": [{"id": config.SEG0_ID, "on": False}]
        })


# -----------------------------------------------------------------------
# Internal helpers
# -----------------------------------------------------------------------
def _build_per_led_i_array(colors: List[RGB]) -> list:
    """
    Build an ``"i"`` array that sets each LED individually.

    Uses run-length encoding: consecutive LEDs with the same color
    are collapsed into a single ``[start, stop, [R,G,B]]`` entry
    to reduce payload size.
    """
    if not colors:
        return []

    i_array: list = []
    run_start = 0
    run_color = colors[0]

    for idx in range(1, len(colors)):
        if colors[idx] != run_color:
            # Flush the current run
            i_array.extend([run_start, idx, list(run_color)])
            run_start = idx
            run_color = colors[idx]

    # Flush the last run
    i_array.extend([run_start, len(colors), list(run_color)])
    return i_array
