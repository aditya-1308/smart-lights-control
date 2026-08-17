Role: IoT Software Architect. Task: Build a highly concurrent "WLED & Tuya Telemetry Controller".

**1. Language Selection**
Select the optimal language (e.g., Go, Rust, C++, Node.js) prioritizing async I/O, low CPU/memory footprint, and thread safety. State your choice briefly.

**2. Hardware Constraints**
* **WLED ESP Board (4 Segments):** Seg 0 runs Prismatik (UDP 21324). To prevent network collisions, this app MUST strictly use WLED's HTTP JSON API (Port 80) for Segments 1, 2, and 3.
* **Homemate Ceiling Light:** Local Tuya-protocol smart bulb for ambient lighting.

**3. Core Modules**
* **API Wrapper:** HTTP client with connection pooling. Silently catch/ignore timeouts.
* **Mod A (Sim Racing):** Concurrent UDP listener for local sim racing RPM data. Map to shift-light gradient on Segs 1 & 2.
* **Mod B (FPS GSI):** Local HTTP server (port 3000) for CS2 JSON POSTs. Triggers: `player:flashed` > 0 = 2s white flash; `player:health` < 20 = red pulse Segs 1 & 2.
* **Mod C (GTA V):** Global OS hotkey listener (e.g., Ctrl+Shift+P) toggling a red/blue police strobe loop on Segs 1 & 2.
* **Mod D (Pomodoro):** 25-min timer thread targeting Seg 3. Modifies WLED 'Percent' effect (fx: 117). Throttle `ix` (intensity) updates to every 2s.
* **Mod E (Tuya Ambient):** Local Tuya control for the ceiling bulb. Sync to game/timer context (e.g., dim red for timer, blue for CS2). Must use slow, smooth 2s color crossfades.

**4. Deliverables (Git Repo Structure)**
Provide the complete project as a Git repository:
1. **Directory Tree.**
2. **Source Code:** Heavily commented, thread-safe, with clear placeholders for IPs/Tuya Keys/UDP structs.
3. **.gitignore:** Language-specific.
4. **LICENSE:** MIT template.
5. **README.md:** Setup, compile commands, and Tuya local key extraction guide.
6. **Git CLI:** Exact terminal commands to init, add, commit, and push to GitHub.