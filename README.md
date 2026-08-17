# RoomLights 💡

A powerful, lightweight Python background app that turns your **WLED LED strip** and **Tuya ceiling light** into an interactive 360° room lighting ecosystem for PC gaming, movies, and desktop use.

---

## 🌟 What RoomLights Does

RoomLights controls **4 distinct hardware zones** synchronously:

| Feature / Situation | Seg 0 (Monitor Perimeter) | Seg 1 + 2 (Lightbar) | Seg 3 (Wall Strip) | Tuya Ceiling Light |
|---|---|---|---|---|
| **Desktop / Movies** | High-speed edge screen sync (**replaces Prismatik**) | Off / Standby | Off / Standby | Real-time screen color ambient (smooth 2s fade) |
| **150+ Chroma PC Games** *(Cyberpunk 2077, Fortnite, Apex, etc.)* | Intercepts game RGB lighting over local port `54235` | In-game action colors | Off / Standby | Dynamic game ambience |
| **Assetto Corsa** | Yellow/Blue/Black flags, track limit flashes, sector split timing sweeps | Progressive rev meter (green→yellow→red→blue flash) | Off / Standby | Warm racing ambience |
| **F1 23 / F1 24** | **3D Proximity Spotter** (left/right car blind-spot alerts), flags, safety car amber | Rev meter fill (telemetry) | Off / Standby | Warm racing ambience |
| **CS2 (Counter-Strike 2)** | Flashbang whiteout, C4 bomb timer flash, low health pulse | Flashbang 2s whiteout & red pulse | Off / Standby | Dark blue gaming ambience |
| **FPS / Action Games** | **Smart ROI**: Detects directional damage blood splatters & illuminates that side | DS4 lightbar colors | Off / Standby | Dynamic game ambience |
| **GTA V / Sony PC Ports** | Game RGB effects | DirectHID DualShock 4 lightbar colors (sirens, health) | Off / Standby | Dynamic game ambience |
| **Pomodoro Timer** | Active background mode | Active background mode | 25-min countdown bar | Dim focus red |

---

## 🛠️ Hardware & Segment Auto-Discovery

RoomLights **automatically queries your WLED board on startup** over Wi-Fi (`/json/state`) to discover segment IDs, exact LED counts, and reversed wiring flags (`"rev": true`).

### Customizable Segment Role Mapping (`.env`):
You can map your WLED segment IDs to any role in `.env` (or set a segment ID to `-1` to disable that feature):

```env
# Role Mapping (Default IDs: 0, 1, 2, 3)
SEGMENT_SCREEN_CAPTURE=0      # Monitor perimeter (Screen capture & spatial effects)
SEGMENT_LIGHTBAR_RIGHT=1      # Right lightbar half (wired R→L)
SEGMENT_LIGHTBAR_LEFT=2       # Left lightbar half (wired L→R)
SEGMENT_POMODORO=3            # Vertical wall strip (Set to -1 to disable)

# Single-Segment Lightbar Option:
# If you have ONE continuous strip for your lightbar (e.g. Seg 1), set:
# SEGMENT_LIGHTBAR=1
```

---

## ⌨️ Customizable Hotkeys

You can control room ambience on the fly using keyboard shortcuts. Hotkeys are completely customizable in `.env`!

| Shortcut (Default) | Function |
|---|---|
| `Ctrl + Shift + L` | Toggle Tuya ceiling light room ambient control ON / OFF |
| `Ctrl + Shift + Up` | Increase Tuya ceiling light brightness by 1% (Instant response) |
| `Ctrl + Shift + Down` | Decrease Tuya ceiling light brightness by 1% (Instant response) |

---

## 🚀 Quick Setup Guide (Beginner Friendly)

### Step 1: Install ViGEmBus Driver (For Controller Lightbar)
Required for games like GTA V and Assetto Corsa to send controller lightbar colors to Python.
1. Download from: [ViGEmBus Releases](https://github.com/nefarius/ViGEmBus/releases)
2. Run the installer and restart your PC.

### Step 2: Disable Steam Input (For DirectHID Games)
1. Open Steam → Right-click your game (e.g. GTA V or Assetto Corsa) → **Properties**.
2. Click **Controller** tab → Set to **Disable Steam Input**.

### Step 3: CS2 Game State Integration (Optional)
Copy the file `gamestate_integration_roomlights.cfg` from this repository into your CS2 cfg folder:
```
<Steam>\steamapps\common\Counter-Strike Global Offensive\game\csgo\cfg\
```

### Step 4: Extract Your Tuya Bulb Local Key
```bash
pip install tinytuya
python -m tinytuya wizard
```
Follow the prompts to sign in with your Tuya developer account. It will print your `device_id`, `ip`, and `local_key`.

### Step 5: Configure `.env`
1. Copy `.env.example` to `.env`:
   ```bash
   copy .env.example .env
   ```
2. Open `.env` in any text editor and fill in your IP addresses and keys:
   ```env
   WLED_IP=192.168.1.100
   TUYA_DEVICE_ID=your_device_id_here
   TUYA_LOCAL_KEY=your_16char_key_here
   TUYA_IP=192.168.1.101
   ```

### Step 6: Install Python Dependencies & Run
```bash
# Activate your virtual environment (if using one)
.venv\Scripts\activate

# Install requirements
pip install -r requirements.txt

# Start RoomLights
python main.py
```
*(Make sure to close/quit Prismatik before running - RoomLights now handles Seg 0 screen capture natively!)*

---

## 🏎️ Sim Racing Setup

- **Assetto Corsa & F1 23 / F1 24**: Both telemetry sources are **auto-detected on the fly**. No `.env` toggle needed!
- **Assetto Corsa with CSP (Custom Shaders Patch)**:
  - Gamepad FX sends rev lights directly to Seg 1+2 automatically.
  - Seg 0 shows flags, track limit warnings, and sector timing sweeps.
- **F1 23 / F1 24 Settings**:
  - Enable UDP telemetry in F1 settings: `Options → Telemetry Settings → UDP Telemetry: ON`, `UDP Port: 20777`, `UDP Format: 2023/2024`.

---

## ❓ Frequently Asked Questions (FAQ) / Troubleshooting

| Issue | Solution |
|---|---|
| **Virtual DS4 fails to start** | Ensure ViGEmBus driver is installed and reboot your PC. |
| **Seg 0 screen capture is laggy** | Adjust `SCREEN_CAPTURE_FPS=24` in `.env`. `dxcam` hardware acceleration is enabled by default. |
| **Chroma games aren't connecting** | Make sure Razer Synapse is **not** running (Synapse blocks port 54235). |
| **Tuya ceiling light not responding** | Verify bulb IP in `.env` and run `python -m tinytuya wizard` to refresh local key. |
| **Single-segment lightbar** | Set `SEGMENT_LIGHTBAR=1` in `.env`. RoomLights will automatically split it down the middle. |
| **How to stop the app** | Press `Ctrl + C` in the terminal window. |

---

## 📜 License

MIT License - free for personal and open-source use. See [LICENSE](LICENSE).
