Act as an expert Systems Software Engineer and IoT Network Architect. I need to build a "Master WLED & Smart Home Telemetry Controller". 

### YOUR FIRST TASK: LANGUAGE SELECTION
Do not default to Python. Evaluate the architecture and requirements below, and select the absolute best programming language for the job. The ideal language must feature:
1. Exceptional handling of concurrent network I/O (listening to UDP, handling HTTP, and controlling Tuya smart devices simultaneously).
2. Extremely low CPU and memory footprint so it runs silently in the background while heavy PC games are active.
3. Non-blocking/Asynchronous networking capabilities.
(Examples of strong candidates might be Go, Rust, C++, or Node.js/TypeScript). 

Briefly state which language you selected and why, then proceed to the coding and repository generation tasks.

### THE HARDWARE & NETWORK ARCHITECTURE
**Device 1: WLED ESP Board** 
Sliced into 4 distinct physical segments (ID 0, 1, 2, 3). 
Segment 0 runs a screen-sync backlight via Prismatik, which aggressively streams UDP data on Port 21324. WLED is configured to lock all incoming UDP traffic strictly to Segment 0. This controller MUST NOT use UDP to communicate with WLED. It must exclusively use WLED's HTTP JSON API (Port 80) to send partial state updates explicitly to Segments 1, 2, and 3 to ensure zero collisions.

**Device 2: Homemate (Tuya-based) Ceiling Light**
A smart ceiling light attached to the back wall used for ambient room lighting. It operates on the local network.

### THE APPLICATION REQUIREMENTS
Write a highly optimized, multithreaded/concurrent application with the following modules:

**1. The WLED API Wrapper**
*   Create a robust HTTP client (reusing connections/sessions).
*   Must accept a payload and send a partial JSON update to WLED's `/json/state` endpoint.
*   Must silently catch and ignore timeouts or connection drops.

**2. Module A: Sim Racing Telemetry Listener (Assetto Corsa / F1)**
*   Create a UDP socket listener running concurrently to intercept raw game telemetry on a local port.
*   Parse incoming RPM/MaxRPM data.
*   Map the RPM to a shift-light color gradient and send the JSON payload to target **Segments 1 and 2**. 

**3. Module B: Game State Integration (CS2 / Tactical FPS)**
*   Create a lightweight local HTTP web server listening on port 3000 to catch GSI JSON POST requests from the game engine.
*   Parse the JSON for specific events: 
    *   If `player:flashed` > 0, flash all segments 100% white for 2 seconds, then restore the previous state.
    *   If `player:health` drops below 20, pulse Segments 1 and 2 Red.

**4. Module C: Custom Hotkey Events (GTA V Police Lights)**
*   Listen for a global OS hotkey trigger (e.g., `Ctrl+Shift+P`).
*   When triggered, run a non-blocking loop that rapidly alternates Segments 1 and 2 between Red and Blue.

**5. Module D: The Pomodoro Productivity Timer**
*   A concurrent task that acts as a 25-minute timer targeting **Segment 3**.
*   Update WLED's built-in `Percent` effect (Effect ID 117). 
*   Calculate the elapsed time percentage and send the corresponding 0-255 value to WLED's `ix` (effect intensity) parameter every 2 seconds.

**6. Module E: Ambient Room Sync (Homemate Ceiling Light)**
*   Implement local control for a Tuya-protocol smart bulb (the Homemate light). 
*   This light acts as background ambiance. It must feature slow, smooth transitions (e.g., a 2-second crossfade) when changing colors.
*   Tie its state to the overall room context (e.g., dim red during a Pomodoro session, dark blue during CS2 gameplay, or matching the dominant ambient color of the racing telemetry). It should update infrequently so as not to overwhelm the Tuya device.

### CODING GUIDELINES
*   Ensure absolute thread/memory safety across all modules.
*   Provide clear comments indicating where I need to insert my WLED IP address, Tuya local key/device IDs, and UDP telemetry structs.
*   Provide any necessary build, compile, or run instructions for the language you selected.

### REPOSITORY & VERSION CONTROL REQUIREMENTS
Package this project into a complete Git repository structure. Provide:
1.  **Directory Structure**: A visual tree representation.
2.  **Source Code**: The complete, heavily commented code separated by file.
3.  **`.gitignore`**: Tailored to the chosen language.
4.  **`LICENSE`**: Include an MIT License template.
5.  **`README.md`**: Project description, prerequisites (like getting Tuya local keys), compile commands, and usage instructions.
6.  **Git Initialization**: A terminal code block with exact commands to initialize the repo, commit, and push to GitHub.