"""
config.py - Central configuration for RoomLights.

Physical WS2812B LED strip layout (total 150 LEDs):
  - Segment 1 (LEDs 0 - 17, 18 LEDs)   : Left bottom horizontal loop (left half of lightbar)
  - Segment 0 (LEDs 17 - 126, 109 LEDs): Whiteboard perimeter (bottom-left -> top-left -> top-right -> bottom-right)
  - Segment 2 (LEDs 126 - 144, 18 LEDs): Right bottom horizontal loop (right half of lightbar)
  - Segment 3 (LEDs 144 - 150, 6 LEDs) : Wall Pomodoro timer

WLED Realtime Configuration:
  - "Use main segment only" enabled in WLED with Segment 0 as Main Segment.
  - Seg 0 receives UDP DNRGB frames directly on Port 21324.
  - Segments 1, 2, 3 update via HTTP JSON API (/json/state) with partial payloads.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Load .env from project root
# ---------------------------------------------------------------------------
_env_path = Path(__file__).resolve().parent / ".env"
if not _env_path.exists():
    print(f"[CONFIG] WARNING: .env file not found at {_env_path}")
    print("[CONFIG]          Copy .env.example to .env and fill in your values.")
load_dotenv(dotenv_path=_env_path, override=True)


def _get(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _int(key: str, default: int = 0) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default


def _float(key: str, default: float = 0.0) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        return default


# ===================================================================
# WLED
# ===================================================================
WLED_IP: str = _get("WLED_IP", "192.168.1.100")
WLED_PORT: int = 80          # HTTP API port (fixed by WLED firmware)
WLED_UDP_PORT: int = 21324   # WLED Realtime UDP DNRGB port
WLED_BASE_URL: str = f"http://{WLED_IP}:{WLED_PORT}"
WLED_STATE_URL: str = f"{WLED_BASE_URL}/json/state"
WLED_TIMEOUT: float = 1.0    # seconds

# ---------------------------------------------------------------------------
# Segment definitions - physical LED layout
# ---------------------------------------------------------------------------
# Seg 0: 109 LEDs (physical 17..126) - Screen ambient display
#   Counter-clockwise perimeter from bottom-left:
#   idx  0-17  : Left edge   (18 LEDs, bottom-left  -> top-left)
#   idx 18-53  : Top edge    (36 LEDs, top-left     -> top-right)
#   idx 54-71  : Right edge  (18 LEDs, top-right    -> bottom-right)
#   idx 72-108 : Bottom edge (37 LEDs, bottom-right -> bottom-left)
#
# Seg 1: 18 LEDs (physical 0..17)    - Left half of unified lightbar
# Seg 2: 18 LEDs (physical 126..144) - Right half of unified lightbar
# Seg 3:  6 LEDs (physical 144..150) - Pomodoro wall strip (-1 to disable)
#
SEG0_ID: int = _int("SEGMENT_SCREEN_CAPTURE", 0)   # Screen ambient (Main segment on UDP)
SEG1_ID: int = _int("SEGMENT_LIGHTBAR_LEFT", 1)    # Left lightbar half (LEDs 0..17)
SEG2_ID: int = _int("SEGMENT_LIGHTBAR_RIGHT", 2)   # Right lightbar half (LEDs 126..144)
SEG_LIGHTBAR_ID: int = _int("SEGMENT_LIGHTBAR", -1) # Single lightbar alternative (-1 if using dual-bar)
SEG3_ID: int = _int("SEGMENT_POMODORO", 3)         # Pomodoro timer (LEDs 144..150)

# Physical LED Start Offset for Screen Ambient Capture Stream (default 17)
SEG0_START_LED: int = _int("SEGMENT_0_START_LED", 17)
# Prismatik Profile Name or Full Path (e.g. Movies.ini, Gaming.ini, Lightpack.ini)
PRISMATIK_PROFILE: str = _get("PRISMATIK_PROFILE", "Movies.ini")

SEG0_COUNT: int = 109
SEG1_COUNT: int = 18
SEG2_COUNT: int = 18
SEG3_COUNT: int = 6
LIGHTBAR_TOTAL: int = SEG1_COUNT + SEG2_COUNT  # 36 LEDs logical bar (0..17 left, 18..35 right)

# Seg 0 edge slice indices (109 total)
SEG0_LEFT: tuple   = (0, 18)     # idx 0-17  (18 LEDs)
SEG0_TOP: tuple    = (18, 54)    # idx 18-53 (36 LEDs)
SEG0_RIGHT: tuple  = (54, 72)    # idx 54-71 (18 LEDs)
SEG0_BOTTOM: tuple = (72, 109)   # idx 72-108 (37 LEDs)

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
# Sim Racing Telemetry Ports (100% UDP - zero shared memory crashes)
# ===================================================================
SIM_GAME: str = _get("SIM_GAME", "auto")  # auto | ac | acc | f1 | ams2 | forza | iracing
AC_UDP_PORT:    int = _int("AC_UDP_PORT",    9996)   # Assetto Corsa native UDP port
F1_UDP_PORT:    int = _int("F1_UDP_PORT",    20777)  # F1 23/24 UDP port
AMS2_UDP_PORT:  int = _int("AMS2_UDP_PORT",  5606)   # AMS2 / PCARS2 UDP port
FORZA_UDP_PORT: int = _int("FORZA_UDP_PORT", 5300)   # Forza Data Out UDP port

# ---------------------------------------------------------------------------
# Rev meter thresholds (for 36-LED logical bar: 0..17 left, 18..35 right)
# ---------------------------------------------------------------------------
REV_START_PCT: float = 0.28
REV_GREEN_PCT: float = 0.50
REV_YELLOW_PCT: float = 0.68
REV_FULL_PCT: float = 0.82
REV_LIMITER_PCT: float = 0.93
REV_FLASH_HZ: int = 4

# Outer -> Inner rev meter layout on 36-LED bar:
REV_GREEN_ZONE  = list(range(0, 7)) + list(range(29, 36))    # outer tips
REV_YELLOW_ZONE = list(range(7, 16)) + list(range(20, 29))   # middle
REV_RED_ZONE    = list(range(16, 20))                         # center 4 LEDs

# ===================================================================
# DS4 Virtual Controller
# ===================================================================
# Set to true ONLY if you want to emulate a DualShock 4 controller for
# Sony PC games (GTA V, Spider-Man). Set to false when using an Xbox controller
# so games don't prioritize the virtual DS4 over your physical Xbox controller.
ENABLE_VIRTUAL_DS4: bool = _get("ENABLE_VIRTUAL_DS4", "false").lower() in ("true", "1", "yes")
DS4_LIGHTBAR_TIMEOUT: float = 3.0

# ===================================================================
# Pomodoro Timer
# ===================================================================
POMODORO_DURATION_MIN: int = _int("POMODORO_DURATION_MIN", 25)
POMODORO_DURATION_SEC: int = POMODORO_DURATION_MIN * 60
POMODORO_UPDATE_INTERVAL: float = 2.0

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
TUYA_CROSSFADE_DURATION: float = 2.0
TUYA_CROSSFADE_STEPS: int = 20

# ===================================================================
# General
# ===================================================================
LIGHTBAR_UPDATE_HZ: int = 30

# ===================================================================
# Screen Capture (Seg 0 Realtime UDP)
# ===================================================================
# Defaults to 24 FPS (or value configured in .env)
SCREEN_CAPTURE_FPS: int = _int("SCREEN_CAPTURE_FPS", 24)
SCREEN_CAPTURE_GAMMA: float = _float("SCREEN_CAPTURE_GAMMA", 1.8)
SCREEN_CAPTURE_SATURATION: float = _float("SCREEN_CAPTURE_SATURATION", 1.3)
SCREEN_CAPTURE_SWAP_RGB: bool = _get("SCREEN_CAPTURE_SWAP_RGB", "false").lower() in ("true", "1", "yes")

# ===================================================================
# Chroma SDK Bridge
# ===================================================================
CHROMA_PORT: int = 54235
CHROMA_HEARTBEAT_TIMEOUT: float = 5.0

# ===================================================================
# Smart ROI
# ===================================================================
ROI_DAMAGE_MARGIN_PCT: float = 0.15
ROI_DAMAGE_DEPTH_PCT: float = 0.10
ROI_RED_THRESHOLD: int = 150
ROI_RED_DOMINANCE: float = 1.5

# ===================================================================
# Keybinds
# ===================================================================
KEYBIND_TUYA_TOGGLE: str = _get("KEYBIND_TUYA_TOGGLE", "<ctrl>+<shift>+l")
KEYBIND_TUYA_BRIGHTNESS_UP: str = _get("KEYBIND_TUYA_BRIGHTNESS_UP", "<ctrl>+<shift>+<up>")
KEYBIND_TUYA_BRIGHTNESS_DOWN: str = _get("KEYBIND_TUYA_BRIGHTNESS_DOWN", "<ctrl>+<shift>+<down>")
