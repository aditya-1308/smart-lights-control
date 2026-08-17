"""
config.py - Central configuration for RoomLights.

Reads all settings from a .env file in the project root.
Every other module imports from here - never reads os.environ directly.
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
WLED_TIMEOUT: float = 1.0  # seconds - silently drop on timeout

# ---------------------------------------------------------------------------
# Segment definitions - physical LED layout
# ---------------------------------------------------------------------------
# Seg 0: 109 LEDs - screen edge ambient / spatial game effects
#   Clockwise from bottom-middle:
#   idx  0-17  : bottom-right (18 LEDs, center → right corner)
#   idx 18-35  : right edge   (18 LEDs, bottom-right → top-right)
#   idx 36-71  : top edge     (36 LEDs, top-right → top-left)
#   idx 72-89  : left edge    (18 LEDs, top-left → bottom-left)
#   idx 90-108 : bottom-left  (19 LEDs, bottom-left → center)
#
# Seg 1:  17 LEDs - right half of lightbar (runs R→L, idx 0 = far right)
# Seg 2:  18 LEDs - left half of lightbar  (runs L→R, idx 0 = far left)
# Seg 3:   6 LEDs - Pomodoro bar (vertical on wall, top→bottom)
#
# Seg 1 + Seg 2 = one unified 35-LED logical bar.
SEG0_ID: int = 0
SEG0_COUNT: int = 109
SEG0_BOTTOM_RIGHT: tuple = (0, 18)    # idx 0-17,  18 LEDs
SEG0_RIGHT: tuple = (18, 36)           # idx 18-35, 18 LEDs
SEG0_TOP: tuple = (36, 72)             # idx 36-71, 36 LEDs
SEG0_LEFT: tuple = (72, 90)            # idx 72-89, 18 LEDs
SEG0_BOTTOM_LEFT: tuple = (90, 109)    # idx 90-108, 19 LEDs

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
# Sim Racing (Auto-detects Assetto Corsa & F1 23/24)
# ===================================================================
F1_UDP_PORT: int = _int("F1_UDP_PORT", 20777)


# ---------------------------------------------------------------------------
# Rev meter thresholds - tune per car / preference
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

# ===================================================================
# Screen Capture (replaces Prismatik for Seg 0)
# ===================================================================
SCREEN_CAPTURE_FPS: int = _int("SCREEN_CAPTURE_FPS", 24)
SCREEN_EDGE_DEPTH_PCT: float = 0.08  # 8% of screen dimension for edge strip
SCREEN_DOWNSCALE_WIDTH: int = 320     # downscale target for performance
SCREEN_DOWNSCALE_HEIGHT: int = 180

# ===================================================================
# Chroma SDK Bridge (intercepts 150+ games' RGB data)
# ===================================================================
CHROMA_PORT: int = 54235
CHROMA_HEARTBEAT_TIMEOUT: float = 5.0  # seconds without heartbeat → session dead

# ===================================================================
# Smart ROI (directional damage, minimap, health bar detection)
# ===================================================================
ROI_DAMAGE_MARGIN_PCT: float = 0.15   # inner margin for damage vignette detection
ROI_DAMAGE_DEPTH_PCT: float = 0.10    # depth of detection strips
ROI_RED_THRESHOLD: int = 150          # red channel threshold for damage flash
ROI_RED_DOMINANCE: float = 1.5        # R must be this much > G and B

# ===================================================================
# Keybinds / Hotkeys (pynput format, configurable via .env)
# ===================================================================
KEYBIND_TUYA_TOGGLE: str = _get("KEYBIND_TUYA_TOGGLE", "<ctrl>+<shift>+l")
KEYBIND_TUYA_BRIGHTNESS_UP: str = _get("KEYBIND_TUYA_BRIGHTNESS_UP", "<ctrl>+<shift>+<up>")
KEYBIND_TUYA_BRIGHTNESS_DOWN: str = _get("KEYBIND_TUYA_BRIGHTNESS_DOWN", "<ctrl>+<shift>+<down>")

