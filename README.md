# RoomLights 💡

A high-performance **100% Native C++ & Python** ambient room lighting engine that unifies your **WLED addressable LED strip** and **Tuya smart ceiling light** into an ultra-responsive, real-time 360° interactive room lighting ecosystem for PC gaming, sim racing, movies, and desktop productivity.

---

## 🌟 Key Highlights & Features

- ⚡ **100% Native C++ DirectX 11 Capture Engine (`roomlights_capture.exe`):**
  Uses DXGI Desktop Duplication with direct GPU staging copy and Prismatik 4-pixel strided accumulation to achieve rock-solid **60 FPS** with **< 0.5ms latency** and zero CPU/GPU stalls.
- 🌐 **Universal Dynamic Hardware Auto-Discovery:**
  **No hardcoded LED counts or segment bounds!** RoomLights automatically queries your WLED board on boot over HTTP (`/json/state`), discovers all physical segment start indices, lengths, and reversed wiring flags, and dynamically scales the single UDP DNRGB frame (`4 + total_leds * 3` bytes) to fit your exact setup.
- 🎨 **Dedicated Calibration Profiles (`profiles/`):**
  Drop any Prismatik `.ini` profile directly into the `profiles/` directory (or use the built-in `profiles/Movies.ini`). RoomLights automatically scales and interpolates the profile zones to match your screen segment's exact LED count.
- 🏎️ **Sim Racing Rev Meter with Adaptive Redline Scaling:**
  - **Assetto Corsa:** Directly reads Windows Kernel Shared Memory (`acpmf_physics` & `acpmf_static`) in C++ with percentage-based tachometer scaling (F1, GT3, LMP, and road cars automatically start at 65% RPM, full revs at 95%, and flash shift lights at 96%+ / pit limiter).
  - **F1 23/24/25, Automobilista 2, Forza Horizon/Motorsport, iRacing:** Zero-latency Windows Shared Memory IPC bridge connects telemetry directly to the C++ 60 FPS renderer.
- 🎮 **PlayStation DualSense / DualShock 4 Lightbar Emulation:**
  Auto-detects when PlayStation PC games launch (*Spider-Man*, *God of War*, *Cyberpunk 2077*, *The Last of Us*, *Death Stranding*, *Horizon Zero Dawn*, etc.), attaches a virtual DS4 controller, and routes in-game lightbar colors directly to your strip.
- 💥 **CS2 (Counter-Strike 2) Game State Integration:**
  Full-screen whiteout flashbang animations, low-health red pulses, and C4 bomb timer synchronization.
- 🌈 **Razer Chroma REST API Bridge:**
  Emulates the Razer Chroma SDK over local port `54235` to sync lighting with 150+ Chroma-supported PC games without requiring Razer Synapse.
- 💡 **Tuya Smart Ceiling Light Sync & Hotkeys:**
  Controls your Tuya ceiling bulb locally with keyboard hotkeys and automatically dims/switches ambience during gaming.

---

## 📐 Multi-Zone Segment Architecture

RoomLights can drive any 1, 2, 3, or 4 segment layout configured in WLED:

| Segment Role | Default ID | Description |
| :--- | :--- | :--- |
| **Screen Ambient** | `SEGMENT_SCREEN_CAPTURE=0` | Monitor/whiteboard perimeter running 60 FPS DirectX screen capture. |
| **Left Lightbar** | `SEGMENT_LIGHTBAR_LEFT=1` | Left half of telemetry rev meter & DS4 lightbar (mirrored outer-to-inner). |
| **Right Lightbar** | `SEGMENT_LIGHTBAR_RIGHT=2` | Right half of telemetry rev meter & DS4 lightbar (mirrored outer-to-inner). |
| **Single Lightbar** | `SEGMENT_LIGHTBAR=-1` | Alternative single continuous left-to-right lightbar (if not using dual-bar). |
| **Aux / Pomodoro** | `SEGMENT_POMODORO=3` | Auxiliary strip for 25-minute Pomodoro focus progress or status indicators. |

---

## 🏎️ Sim Racing Telemetry Ports

RoomLights connects directly to racing sims with zero game modification:

| Game | Protocol / Port | Setup in Game |
| :--- | :--- | :--- |
| **Assetto Corsa** | Shared Memory (`acpmf_physics`) | Fully automatic (zero setup needed) |
| **F1 23 / F1 24 / F1 25** | UDP Port `20777` | `Telemetry Settings → UDP: ON, Port: 20777` |
| **Automobilista 2 (AMS2)** | UDP Port `5606` | `Options → Telemetry: Broadcast ON, Port: 5606` |
| **Forza Motorsport / Horizon** | UDP Port `5300` | `HUD → Data Out: ON, IP: 127.0.0.1, Port: 5300` |

---

## 🚀 Quick Setup Guide

### 1. Prerequisites
- **Windows 10 / 11**
- **Python 3.12+**
- **ViGEmBus Driver** *(Required for controller lightbar passthrough)*: [Download from GitHub](https://github.com/nefarius/ViGEmBus/releases)
- Any **WLED-enabled ESP8266 / ESP32 controller** with addressable LED strip (WS2812B, SK6812, WS2815, etc.).

### 2. Installation
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

### 3. Configuration (`.env`)
Open `.env` in a text editor and set your WLED IP:
```ini
# IP Address of your WLED device on your local network
WLED_IP=192.168.1.100

# Screen capture target framerate (default 60)
SCREEN_CAPTURE_FPS=60

# Prismatik profile name placed in the profiles/ folder
PRISMATIK_PROFILE=Movies.ini

# WLED Segment IDs (match the segment indices in your WLED web UI)
SEGMENT_SCREEN_CAPTURE=0
SEGMENT_LIGHTBAR_LEFT=1
SEGMENT_LIGHTBAR_RIGHT=2
SEGMENT_LIGHTBAR=-1
SEGMENT_POMODORO=3
```

### 4. Calibration Profile (`profiles/`)
- Paste your custom Prismatik calibration `.ini` file into the [`profiles/`](file:///profiles/) directory (e.g. `profiles/MyCustomMonitor.ini`).
- Set `PRISMATIK_PROFILE=MyCustomMonitor.ini` in `.env`.
- RoomLights will automatically scale and interpolate the screen sampling zones to match your segment's exact LED count!

### 5. Running RoomLights
```powershell
python main.py
```
*(Or use the provided batch runner script to launch automatically on startup).*

---

## ⌨️ Hotkeys & Shortcuts

| Shortcut | Function |
| :--- | :--- |
| `Ctrl + Shift + L` | Toggle Tuya ceiling light ambient control ON / OFF |
| `Ctrl + Shift + Up` | Increase Tuya ceiling light brightness by 1% |
| `Ctrl + Shift + Down` | Decrease Tuya ceiling light brightness by 1% |

---

## 📜 License

MIT License — free for personal and open-source use.
