# RoomLights

A lightweight Python background app that intelligently controls your WLED LED strip and Tuya ceiling bulb based on game telemetry, a virtual DS4 controller lightbar, and screen sync.

## What It Does

| Situation | Seg 1 + Seg 2 (lightbar) | Seg 3 (wall) | Ceiling |
|---|---|---|---|
| Idle / desktop | Off | Off | Warm white |
| GTA V (wanted level) | Red/blue flash from DS4 | Off | Adapts |
| AC + CSP (driving) | Green→yellow→red→blue shift lights | Off | Warm amber |
| F1 23/24 (driving) | Rev meter fill (telemetry) | Off | Warm amber |
| CS2 (flashbang) | All white 2s, then restore | Off | Dark blue |
| CS2 (health < 20) | Red breathing pulse | Off | Dark blue |
| Pomodoro timer | Off | 6-LED countdown bar | Dim warm red |
| Any other game | DS4 lightbar color (if supported) | Off | Adapts |

## Hardware

- **WLED ESP board** — 4 segments:
  - Seg 0 (109 LEDs): Prismatik screen sync — untouched by this app
  - Seg 1 (17 LEDs): Right half of lightbar (wired right→left)
  - Seg 2 (18 LEDs): Left half of lightbar (wired left→right)
  - Seg 3 (6 LEDs): Vertical strip on wall (top→bottom)
- **Homemate / Tuya ceiling bulb** on local network

---

## One-Time Setup

### 1. Install ViGEmBus Driver

Required for the virtual DS4 controller (which captures lightbar colors from games).

1. Download from: https://github.com/nefarius/ViGEmBus/releases
2. Run the installer (one click, requires reboot)

### 2. Configure Prismatik

Prismatik handles your main 109-LED strip (Seg 0). No changes needed — it keeps pointing at your WLED IP as normal.

### 3. Disable Steam Input for DirectHID Games

For GTA V, Assetto Corsa (with CSP), and other Sony PC ports:

1. Open Steam → Library → right-click the game → Properties
2. Go to **Controller** tab
3. Set to **Disable Steam Input**

This lets the game write lightbar data directly to our virtual DS4.

### 4. CS2 Game State Integration

Drop the provided config file into your CS2 game folder:

```
gamestate_integration_roomlights.cfg
→ <Steam>\steamapps\common\Counter-Strike Global Offensive\game\csgo\cfg\
```

CS2 will automatically start sending game state to `http://127.0.0.1:3000/cs2`.

### 5. Get Your Tuya Local Key

```bash
pip install tinytuya
python -m tinytuya wizard
```

Follow the prompts — it will discover your device and print its `device_id`, `ip`, and `local_key`. You need your Tuya Developer account credentials for the wizard.

Alternatively, see the [tinytuya docs](https://github.com/jasonacox/tinytuya#setup-wizard---getting-local-keys).

### 6. Configure .env

```bash
cp .env.example .env
```

Edit `.env` and fill in your values:

```env
WLED_IP=192.168.1.100          # Your WLED board's IP
TUYA_DEVICE_ID=abc123...       # From tinytuya wizard
TUYA_LOCAL_KEY=1234567890abcdef # 16 characters
TUYA_IP=192.168.1.101
TUYA_VERSION=3.3
```

### 7. Install Python Dependencies

```bash
# Create a virtual environment (recommended)
python -m venv .venv
.venv\Scripts\activate          # Windows

pip install -r requirements.txt
```

---

## Running

```bash
python main.py
```

Stop with **Ctrl+C**.

You should see startup logs confirming each module:
```
12:00:00 [INFO] main: RoomLights starting up
12:00:00 [INFO] dualsense: Virtual DS4 created via vgamepad.
12:00:00 [INFO] dualsense: DS4 lightbar callback registered.
12:00:00 [INFO] lightbar: Lightbar renderer started.
12:00:00 [INFO] simracing: Sim racing module starting (SIM_GAME=AC).
12:00:00 [INFO] cs2_gsi: CS2 GSI server listening on http://127.0.0.1:3000/cs2
12:00:00 [INFO] tuya: Tuya bulb connected at 192.168.1.101.
12:00:00 [INFO] main: All modules started. Press Ctrl+C to stop.
```

---

## Sim Racing Notes

**Assetto Corsa with CSP (Custom Shaders Patch):**
- CSP's Gamepad FX scripts write rev light colors directly to our virtual DS4 lightbar.
- You'll see green→yellow→red→blue on Seg 1+2 automatically.
- Make sure Steam Input is **disabled** for AC.

**Assetto Corsa without CSP (base game):**
- The app reads AC's shared memory directly.
- Same visual result, internally computed.

**F1 23 / F1 24:**
- Set `SIM_GAME=F1` in `.env`.
- The game broadcasts UDP telemetry on port 20777 (default).
- Enable UDP telemetry in F1's game settings: `Telemetry → UDP On → Broadcast Mode`.

---

## Tuning the Rev Meter

Edit these constants in `config.py` to match your preferred shift point:

```python
REV_START_PCT   = 0.28   # below this = dark
REV_GREEN_PCT   = 0.50   # green tips fully lit
REV_YELLOW_PCT  = 0.68   # approaching shift zone
REV_FULL_PCT    = 0.82   # all lit = past optimal shift
REV_LIMITER_PCT = 0.93   # blue flash (limiter)
REV_FLASH_HZ    = 4      # flash speed
```

Higher `REV_LIMITER_PCT` = more warning time before limiter flash. Lower `REV_FULL_PCT` = strip fills up earlier (more aggressive).

---

## Git Setup

```bash
git init
git add .
git commit -m "Initial commit: RoomLights telemetry controller"
git remote add origin https://github.com/YOUR_USERNAME/RoomLights.git
git push -u origin main
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Virtual DS4 fails to start | Install ViGEmBus driver, reboot, try again |
| Lightbar color never changes | Disable Steam Input for the game in Steam properties |
| CS2 events not triggering | Check cfg file is in the right folder; verify token matches in .env |
| Tuya not responding | Run `python -m tinytuya wizard` again to get fresh local key |
| F1 UDP no data | Enable UDP telemetry in F1 settings → set port to 20777 |
| AC shared memory not found | AC must be running (not just the launcher) |

---

## License

MIT — see [LICENSE](LICENSE).
