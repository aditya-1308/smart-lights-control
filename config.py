"""
config.py — Central configuration for RoomLights.

Reads all settings from a .env file in the project root.
Every other module imports from here — never reads os.environ directly.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Load .env from project root (same directory as this file)
# ---------------------------------------------------------------------------
_env_path = Path(__file__).resolve().parent / ".env"
if not _env_path.exists():
    print(f"[CONFIG] WARNING: .env file not found at {_env_path}")
    print("[CONFIG]          Copy .env.example to .env and fill in your values.")
load_dotenv(dotenv_path=_env_path, override=True)


def _get(key: str, default: str = "") -> str:
    """Read an env var with a fallback default."""
    return os.getenv(key, default)


def _int(key: str, default: int = 0) -> int:
    """Read an env var as an integer."""
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default


def _float(key: str, default: float = 0.0) -> float:
    """Read an env var as a float."""
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        return default


# ===================================================================
# WLED
# ===================================================================
WLED_IP: str = _get("WLED_IP", "192.168.1.100")
WLED_PORT: int = 80  # HTTP API port (fixed by WLED firmware)
WLED_BASE_URL: str = f"http://{WLED_IP}:{WLED_PORT}"
WLED_STATE_URL: str = f"{WLED_BASE_URL}/json/state"
WLED_TIMEOUT: float = 1.0  # seconds — silently drop on timeout

# ---------------------------------------------------------------------------
# Segment definitions — physical LED layout
# ---------------------------------------------------------------------------
# Seg 0: 109 LEDs — Prismatik screen sync (we NEVER write to this)
# Seg 1:  17 LEDs — right half of lightbar (runs R→L, idx 0 = far right)
# Seg 2:  18 LEDs — left half of lightbar  (runs L→R, idx 0 = far left)
# Seg 3:   6 LEDs — Pomodoro bar (vertical on wall, top→bottom)
#
# Seg 1 + Seg 2 = one unified 35-LED logical bar.
#   Logical pos 0  = Seg2 idx 0  (far physical left)
#   Logical pos 17 = Seg2 idx 17 (inner left)
#   Logical pos 18 = Seg1 idx 16 (inner right)
#   Logical pos 34 = Seg1 idx 0  (far physical right)
SEG1_ID: int = 1
SEG1_COUNT: int = 17
SEG2_ID: int = 2
SEG2_COUNT: int = 18
SEG3_ID: int = 3
SEG3_COUNT: int = 6
LIGHTBAR_TOTAL: int = SEG2_COUNT + SEG1_COUNT  # 35

# ===================================================================
# Tuya Ceiling Light
# ===================================================================
TUYA_DEVICE_ID: str = _get("TUYA_DEVICE_ID", "")
TUYA_LOCAL_KEY: str = _get("TUYA_LOCAL_KEY", "")
TUYA_IP: str = _get("TUYA_IP", "")
TUYA_VERSION: float = _float("TUYA_VERSION", 3.3)

# ===================================================================
# CS2 Game State Integration
# ===================================================================
CS2_GSI_PORT: int = _int("CS2_GSI_PORT", 3000)
CS2_GSI_TOKEN: str = _get("CS2_GSI_TOKEN", "roomlights_secret_token_123")

# ===================================================================
# Sim Racing (rev meter fallback when DS4 lightbar is unavailable)
# ===================================================================
SIM_GAME: str = _get("SIM_GAME", "AC").upper()  # "AC" or "F1"
F1_UDP_PORT: int = _int("F1_UDP_PORT", 20777)

# ---------------------------------------------------------------------------
# Rev meter thresholds — tune per car / preference
# ---------------------------------------------------------------------------
REV_START_PCT: float = 0.28    # below this = dark (no indication)
REV_GREEN_PCT: float = 0.50    # green tips fully lit
REV_YELLOW_PCT: float = 0.68   # yellow zone fully in, approaching shift
REV_FULL_PCT: float = 0.82     # entire strip lit = past optimal shift point
REV_LIMITER_PCT: float = 0.93  # blue flash starts (limiter territory)
REV_FLASH_HZ: int = 4          # blue flash frequency in Hz

# Rev meter zone boundaries on the 35-LED logical bar
# Zone colors are fixed by position; fill level determines how many are lit.
REV_GREEN_ZONE = list(range(0, 7)) + list(range(28, 35))   # 14 LEDs (outer)
REV_YELLOW_ZONE = list(range(7, 16)) + list(range(19, 28))  # 18 LEDs (inner)
REV_RED_ZONE = list(range(16, 19))                           # 3 LEDs  (center)

# ===================================================================
# DS4 Virtual Controller
# ===================================================================
DS4_LIGHTBAR_TIMEOUT: float = 3.0  # seconds of no data → fallback to rev meter

# ===================================================================
# Pomodoro Timer
# ===================================================================
POMODORO_DURATION_MIN: int = _int("POMODORO_DURATION_MIN", 25)
POMODORO_DURATION_SEC: int = POMODORO_DURATION_MIN * 60
POMODORO_UPDATE_INTERVAL: float = 2.0  # seconds between WLED updates

# ===================================================================
# Tuya Ambient Context Colors
# ===================================================================
TUYA_CONTEXT_MAP: dict = {
    "idle":         {"rgb": (255, 200, 120), "brightness": 60},
    "pomodoro":     {"rgb": (180,  30,  10), "brightness": 40},
    "cs2":          {"rgb": (  0,  20, 180), "brightness": 30},
    "racing":       {"rgb": (255, 160,  60), "brightness": 60},
    "generic_game": {"rgb": (100, 100, 180), "brightness": 50},
}
TUYA_CROSSFADE_DURATION: float = 2.0   # seconds
TUYA_CROSSFADE_STEPS: int = 20          # interpolation steps

# ===================================================================
# General
# ===================================================================
LIGHTBAR_UPDATE_HZ: int = 30  # max updates/sec to WLED for the lightbar
