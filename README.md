# RoomLights 💡

A high-performance Python background service that unifies your **WLED addressable LED strip** and **Tuya smart ceiling light** into a responsive, real-time 360° interactive room lighting ecosystem for PC gaming, sim racing, movies, and desktop productivity.

---

## 🌟 Features & Multi-Zone Architecture

RoomLights controls **4 synchronized hardware zones**:

| Feature / Scenario | Seg 0 (Perimeter Strip) | Seg 1 + 2 (Indicator Bar) | Seg 3 (Wall Strip) | Tuya Ceiling Light |
|---|---|---|---|---|
| **Desktop / Movies** | Realtime screen edge ambient (**imports Prismatik calibration**) | Off / Standby | Off / Standby | Smooth screen ambient fade |
| **Sim Racing (AC, F1, AMS2, Forza)** | Track limits, pit status, telemetry cues | 36-LED progressive rev meter (Green $\rightarrow$ Yellow $\rightarrow$ Red $\rightarrow$ Blue Limiter Flash) | Off / Standby | Racing cockpit ambience |
| **150+ Chroma PC Games** *(Cyberpunk, Apex, etc.)* | Intercepts game RGB lighting over local port `54235` | In-game action colors | Off / Standby | Dynamic game ambience |
| **CS2 (Counter-Strike 2)** | Full whiteout flashbang, C4 bomb timer flash, low health pulse | Flashbang whiteout & red health pulse | Off / Standby | Tactical gaming ambience |
| **FPS / Action Games** | **Smart ROI**: Detects directional damage blood splatters & illuminates that side | DS4 controller lightbar | Off / Standby | Dynamic game ambience |
| **DirectHID / DS4 Controller Games** | Game RGB effects | DualShock 4 lightbar passthrough (sirens, health) | Off / Standby | Dynamic game ambience |
| **Pomodoro Focus Timer** | Active background mode | Active background mode | 25-min visual progress bar | Dim focus red |

---

## 📐 Hardware Architecture & Physical Segment Layout

Designed for a 150-LED WS2812B strip split into 4 distinct physical functional zones:

1. **Segment 1 (LEDs 0 – 17, 18 LEDs):** Left bottom horizontal loop (left half of unified bottom telemetry bar).
2. **Segment 0 (LEDs 17 – 126, 109 LEDs):** Full whiteboard/monitor perimeter display (Main Segment for UDP stream).
3. **Segment 2 (LEDs 126 – 144, 18 LEDs):** Right bottom horizontal loop (right half of unified bottom telemetry bar).
4. **Segment 3 (LEDs 144 – 150, 6 LEDs):** Auxiliary utility strip (Pomodoro timer / notification gauge).

### WLED Realtime UDP Protocol Isolation
- **Segment 0** receives continuous high-frequency video capture packets over **Realtime UDP DNRGB (Port 21324)**. WLED's *"Use main segment only"* setting routes UDP packets exclusively to Segment 0.
- **Segments 1, 2, and 3** communicate exclusively with WLED using **HTTP JSON API (`/json/state`)** with partial payload updates (`"id": 1`, `"id": 2`, `"id": 3`), eliminating packet collision and socket contention.

---

## 🏎️ Sim Racing Telemetry (100% Pure UDP — Zero Crash Risk)

RoomLights connects directly to games via UDP network broadcast — **zero shared memory, zero DLL injection, zero game file modification**:

| Game | Protocol | Port | Setup in Game |
| :--- | :--- | :--- | :--- |
| **Assetto Corsa (AC1 & CSP)** | Native AC UDP | `9996` | Auto-connected (zero setup needed) |
| **F1 23 / F1 24** | Codemasters UDP | `20777` | `Telemetry Settings → UDP: ON, Port: 20777` |
| **Automobilista 2 (AMS2)** | Project CARS 2 UDP | `5606` | `Options → Telemetry: Broadcast ON, Port: 5606` |
| **Forza Motorsport / Horizon** | Forza Data Out | `5300` | `HUD → Data Out: ON, IP: 127.0.0.1, Port: 5300` |

---

## 🚀 Quick Setup Guide

### System Requirements
- **Windows 10 / 11**
- **Python 3.12** *(Required for DXcam hardware capture and ViGEmBus controller driver)*
- **ViGEmBus Driver** *(Optional, for controller lightbar passthrough)*: [Download from GitHub](https://github.com/nefarius/ViGEmBus/releases)

### Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/aditya-1308/iot-leds-control.git
   cd iot-leds-control
   ```

2. **Create and activate a Python 3.12 virtual environment:**
   ```bash
   py -3.12 -m venv .venv
   .venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure your hardware:**
   ```bash
   copy .env.example .env
   ```
   Open `.env` and set your `WLED_IP` and optional `TUYA_IP` / keys.

5. **Start RoomLights:**
   ```bash
   python main.py
   ```

---

## ⌨️ Hotkeys & Shortcuts

| Shortcut | Function |
| :--- | :--- |
| `Ctrl + Shift + L` | Toggle Tuya ceiling light room ambient control ON / OFF |
| `Ctrl + Shift + Up` | Increase Tuya ceiling light brightness by 1% |
| `Ctrl + Shift + Down` | Decrease Tuya ceiling light brightness by 1% |

---

## 📜 License

MIT License — free for personal and open-source use.
