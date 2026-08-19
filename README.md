# RoomLights 💡

A high-performance **100% Native C++ & Python** ambient room lighting engine that unifies your **WLED addressable LED strip** and **Tuya smart ceiling light** into an ultra-responsive, real-time 360° interactive room lighting ecosystem for PC gaming, sim racing, movies, and desktop productivity.

> **Built on top of [Prismatik](https://github.com/psieg/Lightpack)** — RoomLights uses Prismatik `.ini` calibration profiles to define the exact screen-sampling zones that map to your physical LED positions around your monitor. You calibrate once in Prismatik, drop the profile file into the `profiles/` folder, and RoomLights handles everything else natively at 60 FPS — no Prismatik process needs to run.

---

## 🌟 Key Features

- ⚡ **100% Native C++ DirectX 11 Capture Engine (`roomlights_capture.exe`):**
  Uses DXGI Desktop Duplication with direct GPU staging copy and Prismatik 4-pixel strided accumulation for rock-solid **60 FPS** with **< 0.5ms latency** and zero CPU/GPU stalls.

- 🌐 **Universal Dynamic Hardware Auto-Discovery:**
  No hardcoded LED counts or segment bounds. RoomLights queries your WLED board on boot over HTTP (`/json/state`), discovers every segment's start, length, and reversed wiring flag, then builds a perfectly-sized UDP DNRGB packet for your exact strip.

- 🎨 **Prismatik Calibration Profile Support (`profiles/`):**
  Calibrate your LEDs once in Prismatik, then drop the `.ini` file into `profiles/`. RoomLights reads the screen-sampling zones directly and scales them to your segment's LED count automatically. No Prismatik installation needed to run.

- 🏎️ **Sim Racing Rev Meter with Adaptive Redline Scaling:**
  - **Assetto Corsa:** Direct Windows Kernel Shared Memory (`acpmf_physics` & `acpmf_static`) — zero game setup.
  - **F1 23 / 24 / 25, AMS2, Forza, iRacing:** Zero-latency Windows Shared Memory IPC bridge.
  - Percentage-based tachometer: all car classes start filling at 65% RPM, full at 95%, flash shift lights at 96%+.

- 🎮 **PlayStation DualSense / DualShock 4 Lightbar Emulation:**
  Auto-detects PlayStation PC games and routes in-game lightbar colors to your strip.

- 💥 **CS2 (Counter-Strike 2) Game State Integration:**
  Flashbang whiteout, low-health pulses, and C4 bomb timer synchronization on your strip.

- 🌈 **Razer Chroma REST API Bridge:**
  Emulates the Chroma SDK on local port `54235` to sync lighting from 150+ Chroma-supported PC games — no Razer hardware needed.

- 💡 **Tuya Smart Ceiling Light Sync & Hotkeys:**
  Controls your Tuya ceiling bulb locally with keyboard shortcuts.

- 🔄 **Clean Exit — Full Strip Restore:**
  When RoomLights closes, it automatically restores all WLED segments to their default presets/effects, clearing any runtime overrides.

---

## 📐 Multi-Zone Segment Architecture

RoomLights supports any 1–4 segment layout configured in WLED:

| Segment Role | Default ID | Description |
| :--- | :--- | :--- |
| **Screen Ambient** | `SEGMENT_SCREEN_CAPTURE=0` | Monitor perimeter — 60 FPS DirectX screen capture |
| **Left Lightbar** | `SEGMENT_LIGHTBAR_LEFT=1` | Left half of rev meter & DS4 lightbar |
| **Right Lightbar** | `SEGMENT_LIGHTBAR_RIGHT=2` | Right half of rev meter & DS4 lightbar |
| **Single Lightbar** | `SEGMENT_LIGHTBAR=-1` | Alternative single continuous lightbar |
| **Aux / Pomodoro** | `SEGMENT_POMODORO=3` | Pomodoro focus timer or status strip |

Set any segment to `-1` in `.env` to disable it.

---

## 🏎️ Sim Racing Telemetry

| Game | Protocol | In-Game Setup |
| :--- | :--- | :--- |
| **Assetto Corsa** | Shared Memory | Automatic — zero setup |
| **F1 23 / 24 / 25** | UDP `20777` | `Telemetry Settings → UDP: ON, Port: 20777` |
| **Automobilista 2** | UDP `5606` | `Options → Telemetry: Broadcast ON, Port: 5606` |
| **Forza Motorsport / Horizon** | UDP `5300` | `HUD → Data Out: ON, IP: 127.0.0.1, Port: 5300` |
| **iRacing** | Shared Memory | Automatic — zero setup |

---

## 🚀 Quick Setup Guide

### 1. Prerequisites

- **Windows 10 / 11**
- **Python 3.12+**
- **MinGW / GCC** *(only if recompiling the C++ engine — pre-built `.exe` is included)*
- **ViGEmBus Driver** *(for PlayStation lightbar emulation)*: [Download from GitHub](https://github.com/nefarius/ViGEmBus/releases)
- **Prismatik** *(only for initial LED calibration — not needed to run RoomLights)*: [Download from GitHub](https://github.com/psieg/Lightpack/releases)
- Any **WLED-enabled ESP8266 / ESP32** with an addressable LED strip (WS2812B, SK6812, WS2815, etc.)

### 2. WLED Setup

1. Flash WLED to your ESP board and connect it to your Wi-Fi network.
2. In the WLED web UI, go to **LED Preferences** and define your segments to match your physical strip layout.
3. Note the **segment IDs** (0, 1, 2, 3…) shown in the WLED UI — you'll reference these in `.env`.

### 3. Calibration Profile (Prismatik)

RoomLights uses **Prismatik `.ini` profiles** to know *exactly which part of the screen* each LED around your monitor should sample.

1. Install Prismatik and run the **LED positioning wizard** — drag each LED zone to match your physical strip layout around your monitor.
2. Save the profile from Prismatik (`Profiles → Save`).
3. **Copy the `.ini` file** into the `profiles/` folder inside this repo (e.g. `profiles/MySetup.ini`).
4. You do **not** need Prismatik running — RoomLights reads the profile file directly.

> A default `profiles/Movies.ini` is included as a starting point for a standard 1080p screen with LEDs around the perimeter.

### 4. Installation

```powershell
# 1. Clone the repository
git clone https://github.com/aditya-1308/iot-leds-control.git
cd iot-leds-control

# 2. Create and activate Python virtual environment
python -m venv .venv
.venv\Scripts\activate

# 3. Install required Python packages
pip install -r requirements.txt

# 4. Copy environment configuration template
copy .env.example .env
```

### 5. Configuration (`.env`)

Open `.env` in a text editor and fill in your values:

```ini
# IP address of your WLED board on your local network
WLED_IP=192.168.1.100

# Screen capture framerate (default 60)
SCREEN_CAPTURE_FPS=60

# Prismatik profile filename (must be in the profiles/ folder)
PRISMATIK_PROFILE=Movies.ini

# WLED Segment IDs — match the segment indices in your WLED web UI
SEGMENT_SCREEN_CAPTURE=0
SEGMENT_LIGHTBAR_LEFT=1
SEGMENT_LIGHTBAR_RIGHT=2
SEGMENT_LIGHTBAR=-1
SEGMENT_POMODORO=3

# Optional: Invert LED direction per segment (true/false)
# Useful if your strip wiring runs the opposite direction to what WLED expects
INVERT_SCREEN_CAPTURE=false
INVERT_LIGHTBAR_LEFT=false
INVERT_LIGHTBAR_RIGHT=false
INVERT_LIGHTBAR=false
INVERT_POMODORO=false
```

> **Note:** Segment length and start/stop values are pulled **automatically** from WLED on every boot. You never need to hardcode LED counts.

### 6. Run

```powershell
python main.py
```

When you exit (`Ctrl+C`), RoomLights automatically restores all segments to their default WLED presets.

---

## ⌨️ Hotkeys & Shortcuts

| Shortcut | Function |
| :--- | :--- |
| `Ctrl + Shift + L` | Toggle Tuya ceiling light ON / OFF |
| `Ctrl + Shift + Up` | Increase Tuya ceiling light brightness by 1% |
| `Ctrl + Shift + Down` | Decrease Tuya ceiling light brightness by 1% |

---

## 📁 Project Structure

```
RoomLights/
├── main.py                  # Entry point — boots all async modules
├── config.py                # Loads and exposes all .env settings
├── state.py                 # Shared runtime state between all modules
├── wled_api.py              # WLED HTTP API client (segment discovery, state restore)
├── wled_udp.py              # Direct UDP DNRGB frame sender
├── ipc_bridge.py            # Zero-latency shared memory IPC (Python ↔ C++)
├── mod_screen_capture.py    # Launches & manages the C++ capture engine
├── mod_lightbar.py          # Rev meter & DS4 lightbar renderer (Seg 1 & 2)
├── mod_a_simracing.py       # Sim racing telemetry (F1, AMS2, Forza, iRacing UDP)
├── mod_b_cs2_gsi.py         # CS2 Game State Integration HTTP server
├── mod_chroma_bridge.py     # Razer Chroma REST API emulator
├── mod_d_pomodoro.py        # Pomodoro focus timer (Seg 3)
├── mod_dualsense.py         # Virtual DS4 / DualSense controller
├── mod_e_tuya.py            # Tuya ceiling light local control
├── mod_seg0_router.py       # Priority-based Seg 0 effect router
├── mod_smart_roi.py         # Directional hit detection for any game
├── mod_spatial_ac.py        # Assetto Corsa spatial effects on Seg 0
├── mod_spatial_f1.py        # F1 proximity spotter & flag effects on Seg 0
├── mod_spatial_cs2.py       # CS2 flashbang / bomb / health effects on Seg 0
├── cpp/
│   └── main.cpp             # 100% Native C++ 60 FPS DXGI capture & UDP engine
├── roomlights_capture.exe   # Pre-compiled C++ engine binary (Windows x64)
├── profiles/
│   └── Movies.ini           # Default Prismatik calibration profile
├── .env.example             # Configuration template
├── requirements.txt         # Python dependencies
└── README.md
```

---

## 📜 License

MIT License — free for personal and open-source use.
