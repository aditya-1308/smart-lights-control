/*
 * roomlights_capture.cpp
 *
 * 100% Native C++ Ambient Screen Capture & Multi-Game Telemetry Engine.
 * Universal Dynamic Hardware Architecture:
 *   - Completely dynamic LED count and segment boundaries (passed at startup from WLED)
 *   - Works out of the box with any strip length and any segment configuration
 *   - Dynamic profile zone scaling (adapts any Prismatik profile to any screen segment size)
 *   - Dynamic Dual-Bar / Single-Bar sim racing rev meter and DS4 lightbar rendering
 *   - Zero-latency Windows Shared Memory IPC Bridge ("RoomLights_IPC")
 *   - High-precision hybrid 60 FPS frame pacer (zero lag spikes)
 */

#pragma comment(lib, "d3d11.lib")
#pragma comment(lib, "dxgi.lib")
#pragma comment(lib, "ws2_32.lib")
#pragma comment(lib, "winmm.lib")

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <mmsystem.h>
#include <d3d11.h>
#include <dxgi1_2.h>
#include <winsock2.h>
#include <ws2tcpip.h>

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>
#include <algorithm>
#include <chrono>

using std::min;
using std::max;

static const int    WLED_PORT         = 21324;
static const float  GAMMA             = 2.004f; // Hardware gamma from Prismatik profile
static const float  SATURATION        = 1.2f;   // Vibrant saturation boost
static const int    KEEPALIVE_SEC     = 5;      // WLED realtime timeout
static const float  DEFAULT_FPS       = 60.0f;
static const int    PIXELS_PER_STEP   = 4;      // Prismatik 4-pixel strided sampling

// Dynamic Hardware Layout Struct
struct HardwareLayout {
    int total_leds   = 150;
    
    // Segment 0: Screen Ambient
    int seg0_start   = 17;
    int seg0_count   = 109;
    
    // Segment 1: Left Lightbar
    int seg1_start   = 0;
    int seg1_count   = 17;
    bool seg1_rev    = false;
    
    // Segment 2: Right Lightbar
    int seg2_start   = 126;
    int seg2_count   = 18;
    bool seg2_rev    = false;
    
    // Single Lightbar Alternative (if seg1 and seg2 are disabled)
    int single_start = -1;
    int single_count = 0;
    bool single_rev  = false;
    
    // Segment 3: Pomodoro / Aux
    int seg3_start   = 144;
    int seg3_count   = 6;
};

// Assetto Corsa Shared Memory Structures (Official Kunos SDK layout)
#pragma pack(push, 4)
struct SPageFilePhysics {
    int   packetId;
    float gas;
    float brake;
    float fuel;
    int   gear;
    int   rpms;
    float steerAngle;
    float speedKmh;
    float velocity[3];
    float accG[3];
    float wheelSlip[4];
    float wheelLoad[4];
    float wheelsPressure[4];
    float wheelAngularSpeed[4];
    float tyreWear[4];
    float tyreDirtyLevel[4];
    float tyreCoreTemperature[4];
    float camberRAD[4];
    float suspensionTravel[4];
    float drs;
    float tc;
    float heading;
    float pitch;
    float roll;
    float cgHeight;
    float carDamage[5];
    int   numberOfTyresOut;
    int   pitLimiterOn;
    float abs;
};

struct SPageFileStatic {
    wchar_t smVersion[15];
    wchar_t acVersion[15];
    int     numberOfSessions;
    int     numCars;
    wchar_t carModel[33];
    wchar_t track[33];
    wchar_t playerName[33];
    wchar_t playerSurname[33];
    wchar_t playerNick[33];
    int     sectorCount;
    float   maxTorque;
    float   maxPower;
    int     maxRpm;
    float   maxFuel;
    float   suspensionMaxTravel[4];
    float   tyreRadius[4];
    float   maxTurboBoost;
};
#pragma pack(pop)

// RoomLights Shared Memory IPC Bridge Struct
#pragma pack(push, 1)
struct RoomLightsIPC {
    uint32_t magic;          // 0x524C4950 ("RLIP")
    uint32_t version;        // 1
    uint32_t sequence;       // Incrementing frame counter
    uint8_t  lightbar_mode;  // 0=NONE, 1=REV_METER, 2=DS4_LIGHTBAR, 3=FULL_ARRAY
    float    rpm_pct;
    uint8_t  is_limiter;
    uint8_t  ds4_r;
    uint8_t  ds4_g;
    uint8_t  ds4_b;
    uint8_t  seg1_rgb[17 * 3]; // 51 bytes
    uint8_t  seg2_rgb[18 * 3]; // 54 bytes
    uint8_t  seg3_rgb[6 * 3];  // 18 bytes
    uint8_t  seg0_override_active;
    uint8_t  seg0_override_r;
    uint8_t  seg0_override_g;
    uint8_t  seg0_override_b;
};
#pragma pack(pop)

struct Zone { int x, y, w, h; };

// Log file handle
static std::ofstream g_logFile;

static void log_msg(const std::string& msg) {
    auto now = std::chrono::system_clock::now();
    auto time_t_now = std::chrono::system_clock::to_time_t(now);
    struct tm tm_buf;
    localtime_s(&tm_buf, &time_t_now);

    char time_str[32];
    std::strftime(time_str, sizeof(time_str), "%Y-%m-%d %H:%M:%S", &tm_buf);

    std::string formatted = "[" + std::string(time_str) + "] [capture-c++] " + msg + "\n";
    std::cout << formatted << std::flush;

    if (g_logFile.is_open()) {
        g_logFile << formatted << std::flush;
    }
}

// ---------------------------------------------------------------------------
// Native 109-LED zone fallback layout from Prismatik Movies.ini profile
// ---------------------------------------------------------------------------
static const Zone MOVIES_PROFILE_ZONES[109] = {
    {986, 813, 52, 116}, {1038, 813, 52, 116}, {1090, 813, 52, 116}, {1142, 813, 52, 116},
    {1194, 813, 52, 116}, {1246, 813, 52, 116}, {1298, 813, 52, 116}, {1350, 813, 52, 116},
    {1402, 813, 52, 116}, {1454, 813, 52, 116}, {1506, 813, 52, 116}, {1558, 813, 52, 116},
    {1610, 813, 52, 116}, {1662, 813, 52, 116}, {1714, 813, 52, 116}, {1766, 813, 52, 116},
    {1818, 813, 51, 116}, {1869, 813, 51, 116}, {1632, 886, 288, 43}, {1632, 843, 288, 43},
    {1632, 800, 288, 43}, {1632, 757, 288, 43}, {1632, 714, 288, 43}, {1632, 671, 288, 43},
    {1632, 628, 288, 43}, {1632, 585, 288, 43}, {1632, 542, 288, 43}, {1632, 499, 288, 43},
    {1632, 456, 288, 43}, {1632, 412, 288, 44}, {1632, 368, 288, 44}, {1632, 324, 288, 44},
    {1632, 280, 288, 44}, {1632, 237, 288, 43}, {1632, 194, 288, 43}, {1632, 151, 288, 43},
    {1867, 151, 53, 116}, {1814, 151, 53, 116}, {1761, 151, 53, 116}, {1708, 151, 53, 116},
    {1655, 151, 53, 116}, {1602, 151, 53, 116}, {1549, 151, 53, 116}, {1496, 151, 53, 116},
    {1443, 151, 53, 116}, {1390, 151, 53, 116}, {1337, 151, 53, 116}, {1284, 151, 53, 116},
    {1230, 151, 54, 116}, {1177, 151, 53, 116}, {1124, 151, 53, 116}, {1071, 151, 53, 116},
    {1018, 151, 53, 116}, {965, 151, 53, 116},  {912, 151, 53, 116},  {859, 151, 53, 116},
    {806, 151, 53, 116},  {753, 151, 53, 116},  {700, 151, 53, 116},  {647, 151, 53, 116},
    {594, 151, 53, 116},  {541, 151, 53, 116},  {488, 151, 53, 116},  {435, 151, 53, 116},
    {382, 151, 53, 116},  {329, 151, 53, 116},  {276, 151, 53, 116},  {223, 151, 53, 116},
    {170, 151, 53, 116},  {117, 151, 53, 116},  {64, 151, 53, 116},   {0, 151, 64, 116},
    {0, 151, 288, 43},    {0, 194, 288, 43},    {0, 237, 288, 43},    {0, 280, 288, 44},
    {0, 324, 288, 44},    {0, 368, 288, 44},    {0, 412, 288, 44},    {0, 456, 288, 43},
    {0, 499, 288, 43},    {0, 542, 288, 43},    {0, 585, 288, 43},    {0, 628, 288, 43},
    {0, 671, 288, 43},    {0, 714, 288, 43},    {0, 757, 288, 43},    {0, 800, 288, 43},
    {0, 843, 288, 43},    {0, 886, 288, 43},    {0, 813, 52, 116},    {52, 813, 52, 116},
    {104, 813, 52, 116},  {156, 813, 52, 116},  {208, 813, 52, 116},  {260, 813, 52, 116},
    {312, 813, 52, 116},  {364, 813, 52, 116},  {416, 813, 52, 116},  {468, 813, 52, 116},
    {520, 813, 52, 116},  {572, 813, 52, 116},  {624, 813, 52, 116},  {676, 813, 52, 116},
    {728, 813, 52, 116},  {780, 813, 52, 116},  {832, 813, 52, 116},  {884, 813, 51, 116},
    {935, 813, 51, 116}
};

// ---------------------------------------------------------------------------
// Dynamic Prismatik Profile Loader (Scales to any screen segment size)
// ---------------------------------------------------------------------------
static std::vector<Zone> load_prismatik_profile(const std::string& requestedProfile, int target_led_count) {
    std::vector<Zone> raw_zones;
    char userPath[MAX_PATH] = {};
    GetEnvironmentVariableA("USERPROFILE", userPath, MAX_PATH);

    std::string profileName = requestedProfile.empty() ? "Movies" : requestedProfile;
    std::string prismatikDir = std::string(userPath) + "\\Prismatik\\Profiles\\";

    std::vector<std::string> candidates;
    if (requestedProfile.find('\\') != std::string::npos || requestedProfile.find('/') != std::string::npos) {
        candidates.push_back(requestedProfile);
    } else {
        // 1. Check local project profiles/ folder
        candidates.push_back("profiles\\" + profileName);
        if (profileName.find(".ini") == std::string::npos) {
            candidates.push_back("profiles\\" + profileName + ".ini");
        }
        candidates.push_back("profiles\\Movies.ini");
        candidates.push_back("profiles\\Lightpack.ini");

        // 2. Check Prismatik directory
        candidates.push_back(prismatikDir + profileName);
        if (profileName.find(".ini") == std::string::npos) {
            candidates.push_back(prismatikDir + profileName + ".ini");
        }
        candidates.push_back(prismatikDir + "Movies.ini");
        candidates.push_back(prismatikDir + "Lightpack.ini");
    }

    bool loaded = false;
    for (auto& path : candidates) {
        std::ifstream f(path);
        if (!f.is_open()) continue;

        raw_zones.clear();
        int px = 0, py = 0, sw = 50, sh = 50;
        bool inLed = false;
        std::string line;

        while (std::getline(f, line)) {
            if (!line.empty() && line.back() == '\r') line.pop_back();

            if (line.rfind("[LED_", 0) == 0) {
                if (inLed) raw_zones.push_back({px, py, sw, sh});
                inLed = true;
                px = py = 0; sw = sh = 50;
            } else if (inLed) {
                if (line.rfind("Position=@Point(", 0) == 0)
                    sscanf_s(line.c_str(), "Position=@Point(%d %d)", &px, &py);
                else if (line.rfind("Size=@Size(", 0) == 0)
                    sscanf_s(line.c_str(), "Size=@Size(%d %d)", &sw, &sh);
            }
        }
        if (inLed) raw_zones.push_back({px, py, sw, sh});

        if (!raw_zones.empty()) {
            log_msg("Loaded " + std::to_string(raw_zones.size()) + " raw zones from " + path);
            loaded = true;
            break;
        }
    }

    if (!loaded || raw_zones.empty()) {
        log_msg("Using embedded Movies.ini profile fallback.");
        raw_zones.assign(MOVIES_PROFILE_ZONES, MOVIES_PROFILE_ZONES + 109);
    }

    if (target_led_count <= 0) target_led_count = (int)raw_zones.size();

    // Dynamically map/interpolate raw profile zones to target screen LED count
    std::vector<Zone> scaled_zones(target_led_count);
    if ((int)raw_zones.size() == target_led_count) {
        scaled_zones = raw_zones;
    } else {
        log_msg("Scaling " + std::to_string(raw_zones.size()) + " profile zones -> " +
                std::to_string(target_led_count) + " target screen LEDs.");
        for (int i = 0; i < target_led_count; i++) {
            float src_idx_f = ((float)i / (float)target_led_count) * (float)raw_zones.size();
            int src_idx = min((int)raw_zones.size() - 1, (int)src_idx_f);
            scaled_zones[i] = raw_zones[src_idx];
        }
    }

    return scaled_zones;
}

// ---------------------------------------------------------------------------
// Hardware Gamma & Saturation Processing
// ---------------------------------------------------------------------------
static inline void process_pixel(float r, float g, float b,
                                  uint8_t& outr, uint8_t& outg, uint8_t& outb) {
    float maxc = (r > g ? r : g) > b ? (r > g ? r : g) : b;
    r = maxc - (maxc - r) * SATURATION;
    g = maxc - (maxc - g) * SATURATION;
    b = maxc - (maxc - b) * SATURATION;
    if (r < 0.0f) r = 0.0f; if (r > 1.0f) r = 1.0f;
    if (g < 0.0f) g = 0.0f; if (g > 1.0f) g = 1.0f;
    if (b < 0.0f) b = 0.0f; if (b > 1.0f) b = 1.0f;
    outr = (uint8_t)(std::pow(r, GAMMA) * 255.0f);
    outg = (uint8_t)(std::pow(g, GAMMA) * 255.0f);
    outb = (uint8_t)(std::pow(b, GAMMA) * 255.0f);
}

// ---------------------------------------------------------------------------
// Assetto Corsa Shared Memory Reader (100% Native C++)
// ---------------------------------------------------------------------------
class ACSharedMemoryReaderCPP {
private:
    HANDLE m_hPhysicsMap = NULL;
    HANDLE m_hStaticMap  = NULL;
    const SPageFilePhysics* m_physicsData = nullptr;
    const SPageFileStatic*  m_staticData  = nullptr;
    int m_maxRpm = 0;
    bool m_connected = false;

public:
    ACSharedMemoryReaderCPP() {}
    ~ACSharedMemoryReaderCPP() { disconnect(); }

    bool connect() {
        if (!m_hPhysicsMap) {
            m_hPhysicsMap = OpenFileMappingA(FILE_MAP_READ, FALSE, "acpmf_physics");
            if (m_hPhysicsMap) {
                m_physicsData = (const SPageFilePhysics*)MapViewOfFile(m_hPhysicsMap, FILE_MAP_READ, 0, 0, sizeof(SPageFilePhysics));
            }
        }

        if (!m_hStaticMap) {
            m_hStaticMap = OpenFileMappingA(FILE_MAP_READ, FALSE, "acpmf_static");
            if (m_hStaticMap) {
                m_staticData = (const SPageFileStatic*)MapViewOfFile(m_hStaticMap, FILE_MAP_READ, 0, 0, sizeof(SPageFileStatic));
            }
        }

        if (m_staticData && m_staticData->maxRpm > 1000) {
            m_maxRpm = m_staticData->maxRpm;
        }

        if (m_physicsData) {
            if (!m_connected) {
                m_connected = true;
                log_msg("Assetto Corsa Shared Memory connected natively in C++!");
            }
            return true;
        }
        return false;
    }

    void disconnect() {
        if (m_physicsData) { UnmapViewOfFile(m_physicsData); m_physicsData = nullptr; }
        if (m_staticData)  { UnmapViewOfFile(m_staticData);  m_staticData = nullptr; }
        if (m_hPhysicsMap) { CloseHandle(m_hPhysicsMap); m_hPhysicsMap = NULL; }
        if (m_hStaticMap)  { CloseHandle(m_hStaticMap);  m_hStaticMap = NULL; }
        m_connected = false;
    }

    bool get_rpm_pct(float& out_pct, bool& out_limiter) {
        if (!connect() || !m_physicsData) {
            return false;
        }

        if (m_physicsData->packetId <= 0 || m_physicsData->rpms <= 0) {
            return false;
        }

        if (m_staticData && m_staticData->maxRpm > 1000) {
            m_maxRpm = m_staticData->maxRpm;
        }

        int rpms = m_physicsData->rpms;
        out_limiter = (m_physicsData->pitLimiterOn != 0);

        if (m_maxRpm <= 0 || rpms > m_maxRpm) {
            m_maxRpm = rpms;
        }

        out_pct = (m_maxRpm > 0) ? ((float)rpms / (float)m_maxRpm) : 0.0f;
        if (out_pct < 0.0f) out_pct = 0.0f;
        if (out_pct > 1.0f) out_pct = 1.0f;

        return true;
    }

    int get_max_rpm() const { return m_maxRpm; }
};

// ---------------------------------------------------------------------------
// RoomLights Shared Memory IPC Reader (Zero Latency C++ Bridge)
// ---------------------------------------------------------------------------
class IPCReaderCPP {
private:
    HANDLE m_hMap = NULL;
    const RoomLightsIPC* m_ipcData = nullptr;
    bool m_connected = false;

public:
    IPCReaderCPP() {}
    ~IPCReaderCPP() { disconnect(); }

    bool connect() {
        if (!m_hMap) {
            m_hMap = OpenFileMappingA(FILE_MAP_READ, FALSE, "RoomLights_IPC");
            if (m_hMap) {
                m_ipcData = (const RoomLightsIPC*)MapViewOfFile(m_hMap, FILE_MAP_READ, 0, 0, sizeof(RoomLightsIPC));
            }
        }
        if (m_ipcData && m_ipcData->magic == 0x524C4950) {
            if (!m_connected) {
                m_connected = true;
                log_msg("RoomLights Shared Memory IPC Bridge connected natively in C++!");
            }
            return true;
        }
        return false;
    }

    void disconnect() {
        if (m_ipcData) { UnmapViewOfFile(m_ipcData); m_ipcData = nullptr; }
        if (m_hMap) { CloseHandle(m_hMap); m_hMap = NULL; }
        m_connected = false;
    }

    const RoomLightsIPC* get_data() {
        if (!m_connected) {
            connect();
        }
        if (m_ipcData && m_ipcData->magic == 0x524C4950) {
            return m_ipcData;
        }
        return nullptr;
    }
};

// ---------------------------------------------------------------------------
// Universal Dynamic Shift Lights Renderer (Dual-Bar & Single-Bar)
// ---------------------------------------------------------------------------
static void render_rev_meter(std::vector<uint8_t>& pkt, const HardwareLayout& hw, float rpm_pct, bool is_limiter, bool flash_state) {
    const float REV_START_PCT = 0.65f;
    const float REV_FULL_PCT  = 0.95f;
    const float REV_LIMIT_PCT = 0.96f;

    uint8_t flash_r = 0, flash_g = 100, flash_b = 255;

    // Helper lambda to set physical LED color
    auto set_led = [&](int physical_idx, uint8_t r, uint8_t g, uint8_t b) {
        if (physical_idx >= 0 && physical_idx < hw.total_leds) {
            int p = 4 + physical_idx * 3;
            pkt[p] = r; pkt[p + 1] = g; pkt[p + 2] = b;
        }
    };

    // Case 1: Limiter / Shift Flash State
    if (is_limiter || rpm_pct >= REV_LIMIT_PCT) {
        uint8_t cr = flash_state ? flash_r : 0;
        uint8_t cg = flash_state ? flash_g : 0;
        uint8_t cb = flash_state ? flash_b : 0;

        if (hw.seg1_start >= 0 && hw.seg2_start >= 0) {
            for (int i = 0; i < hw.seg1_count; i++) set_led(hw.seg1_start + i, cr, cg, cb);
            for (int i = 0; i < hw.seg2_count; i++) set_led(hw.seg2_start + i, cr, cg, cb);
        } else if (hw.single_start >= 0) {
            for (int i = 0; i < hw.single_count; i++) set_led(hw.single_start + i, cr, cg, cb);
        }
        return;
    }

    // Case 2: Below threshold -> Turn off lightbar LEDs
    if (rpm_pct < REV_START_PCT) {
        if (hw.seg1_start >= 0 && hw.seg2_start >= 0) {
            for (int i = 0; i < hw.seg1_count; i++) set_led(hw.seg1_start + i, 0, 0, 0);
            for (int i = 0; i < hw.seg2_count; i++) set_led(hw.seg2_start + i, 0, 0, 0);
        } else if (hw.single_start >= 0) {
            for (int i = 0; i < hw.single_count; i++) set_led(hw.single_start + i, 0, 0, 0);
        }
        return;
    }

    float span = REV_FULL_PCT - REV_START_PCT;
    float norm = (span > 0.0f) ? max(0.0f, min(1.0f, (rpm_pct - REV_START_PCT) / span)) : 0.0f;

    // Dual Bar Mode (Outer tips -> Center redline)
    if (hw.seg1_start >= 0 && hw.seg2_start >= 0) {
        int lit_left  = (int)std::round(norm * (float)hw.seg1_count);
        int lit_right = (int)std::round(norm * (float)hw.seg2_count);

        // Left Bar
        for (int i = 0; i < hw.seg1_count; i++) {
            int led_offset = hw.seg1_rev ? (hw.seg1_count - 1 - i) : i;
            int phys_idx = hw.seg1_start + led_offset;

            if (i < lit_left) {
                float pos_pct = (float)i / (float)hw.seg1_count;
                uint8_t cr = 0, cg = 0, cb = 0;
                if (pos_pct < 0.35f)       { cr = 0;   cg = 255; cb = 0; }   // Green outer tip
                else if (pos_pct < 0.75f)  { cr = 255; cg = 165; cb = 0; } // Yellow middle
                else                       { cr = 255; cg = 0;   cb = 0; } // Red center
                set_led(phys_idx, cr, cg, cb);
            } else {
                set_led(phys_idx, 0, 0, 0);
            }
        }

        // Right Bar
        for (int i = 0; i < hw.seg2_count; i++) {
            int led_offset = hw.seg2_rev ? i : (hw.seg2_count - 1 - i);
            int phys_idx = hw.seg2_start + led_offset;

            if (i < lit_right) {
                float pos_pct = (float)i / (float)hw.seg2_count;
                uint8_t cr = 0, cg = 0, cb = 0;
                if (pos_pct < 0.35f)       { cr = 0;   cg = 255; cb = 0; }   // Green outer tip
                else if (pos_pct < 0.75f)  { cr = 255; cg = 165; cb = 0; } // Yellow middle
                else                       { cr = 255; cg = 0;   cb = 0; } // Red center
                set_led(phys_idx, cr, cg, cb);
            } else {
                set_led(phys_idx, 0, 0, 0);
            }
        }
    }
    // Single Bar Mode (Left -> Right continuous sweep)
    else if (hw.single_start >= 0) {
        int lit = (int)std::round(norm * (float)hw.single_count);
        for (int i = 0; i < hw.single_count; i++) {
            int led_offset = hw.single_rev ? (hw.single_count - 1 - i) : i;
            int phys_idx = hw.single_start + led_offset;

            if (i < lit) {
                float pos_pct = (float)i / (float)hw.single_count;
                uint8_t cr = 0, cg = 0, cb = 0;
                if (pos_pct < 0.35f)       { cr = 0;   cg = 255; cb = 0; }
                else if (pos_pct < 0.75f)  { cr = 255; cg = 165; cb = 0; }
                else                       { cr = 255; cg = 0;   cb = 0; }
                set_led(phys_idx, cr, cg, cb);
            } else {
                set_led(phys_idx, 0, 0, 0);
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------
int main(int argc, char* argv[]) {
    g_logFile.open("roomlights_capture.log", std::ios::out | std::ios::app);

    std::string wled_ip     = "10.103.233.251";
    float target_fps        = DEFAULT_FPS;
    std::string profile_req = "Movies.ini";
    HardwareLayout hw;

    // CLI Parameter Parsing:
    // argv[1]: wled_ip
    // argv[2]: target_fps
    // argv[3]: profile_req
    // argv[4]: total_leds
    // argv[5]: seg0_start, argv[6]: seg0_count
    // argv[7]: seg1_start, argv[8]: seg1_count, argv[9]: seg1_rev
    // argv[10]: seg2_start, argv[11]: seg2_count, argv[12]: seg2_rev
    // argv[13]: single_start, argv[14]: single_count, argv[15]: single_rev
    // argv[16]: seg3_start, argv[17]: seg3_count
    if (argc > 1) wled_ip = argv[1];
    if (argc > 2) target_fps = (float)std::atof(argv[2]);
    if (argc > 3) profile_req = argv[3];
    if (argc > 4) hw.total_leds = std::atoi(argv[4]);
    if (argc > 6) { hw.seg0_start = std::atoi(argv[5]); hw.seg0_count = std::atoi(argv[6]); }
    if (argc > 9) { hw.seg1_start = std::atoi(argv[7]); hw.seg1_count = std::atoi(argv[8]); hw.seg1_rev = (std::atoi(argv[9]) != 0); }
    if (argc > 12) { hw.seg2_start = std::atoi(argv[10]); hw.seg2_count = std::atoi(argv[11]); hw.seg2_rev = (std::atoi(argv[12]) != 0); }
    if (argc > 15) { hw.single_start = std::atoi(argv[13]); hw.single_count = std::atoi(argv[14]); hw.single_rev = (std::atoi(argv[15]) != 0); }
    if (argc > 17) { hw.seg3_start = std::atoi(argv[16]); hw.seg3_count = std::atoi(argv[17]); }

    if (target_fps <= 0.0f) target_fps = DEFAULT_FPS;
    if (hw.total_leds <= 0) hw.total_leds = 150;

    timeBeginPeriod(1);
    log_msg("=== RoomLights 100% Native C++ Universal Engine Started ===");
    log_msg("Target WLED: " + wled_ip + ":" + std::to_string(WLED_PORT));
    log_msg("Dynamic Total Strip Length: " + std::to_string(hw.total_leds) + " LEDs");
    log_msg("Seg 0 (Screen Ambient): Start=" + std::to_string(hw.seg0_start) + ", Count=" + std::to_string(hw.seg0_count));
    if (hw.seg1_start >= 0 && hw.seg2_start >= 0) {
        log_msg("Dual Lightbar: Seg1 Start=" + std::to_string(hw.seg1_start) + ", Count=" + std::to_string(hw.seg1_count) +
                (hw.seg1_rev ? " [REV]" : "") + " | Seg2 Start=" + std::to_string(hw.seg2_start) + ", Count=" + std::to_string(hw.seg2_count) +
                (hw.seg2_rev ? " [REV]" : ""));
    } else if (hw.single_start >= 0) {
        log_msg("Single Lightbar: Start=" + std::to_string(hw.single_start) + ", Count=" + std::to_string(hw.single_count));
    }
    if (hw.seg3_start >= 0) {
        log_msg("Seg 3 (Aux/Pomodoro): Start=" + std::to_string(hw.seg3_start) + ", Count=" + std::to_string(hw.seg3_count));
    }

    WSADATA wsa;
    WSAStartup(MAKEWORD(2, 2), &wsa);
    SOCKET sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    u_long nonblock = 1; ioctlsocket(sock, FIONBIO, &nonblock);

    sockaddr_in dest{};
    dest.sin_family = AF_INET;
    dest.sin_port   = htons(WLED_PORT);
    inet_pton(AF_INET, wled_ip.c_str(), &dest.sin_addr);

    // Dynamically load & scale profile zones to match the discovered screen segment count
    auto zones = load_prismatik_profile(profile_req, hw.seg0_count);

    // Dynamically allocate WLED UDP DNRGB packet buffer
    std::vector<uint8_t> pkt(4 + hw.total_leds * 3, 0);
    pkt[0] = 0x04;
    pkt[1] = (uint8_t)KEEPALIVE_SEC;
    pkt[2] = 0x00; // Start at LED offset 0
    pkt[3] = 0x00;

    ID3D11Device*        d3dDev  = nullptr;
    ID3D11DeviceContext* d3dCtx  = nullptr;
    D3D_FEATURE_LEVEL    fl;
    HRESULT hr = D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr,
                                   0, nullptr, 0, D3D11_SDK_VERSION,
                                   &d3dDev, &fl, &d3dCtx);
    if (FAILED(hr)) {
        log_msg("CRITICAL: D3D11CreateDevice failed: 0x" + std::to_string(hr));
        return 1;
    }

    IDXGIDevice*              dxgiDev  = nullptr;
    IDXGIAdapter*             adapter  = nullptr;
    IDXGIOutput*              output   = nullptr;
    IDXGIOutput1*             output1  = nullptr;
    IDXGIOutputDuplication*   dupl     = nullptr;

    d3dDev->QueryInterface(__uuidof(IDXGIDevice), (void**)&dxgiDev);
    dxgiDev->GetParent(__uuidof(IDXGIAdapter), (void**)&adapter);
    adapter->EnumOutputs(0, &output);
    output->QueryInterface(__uuidof(IDXGIOutput1), (void**)&output1);

    // Initial DXGI Desktop Duplication attach
    int retry_count = 0;
    while (FAILED(hr = output1->DuplicateOutput(d3dDev, &dupl))) {
        retry_count++;
        char hex_buf[32];
        sprintf_s(hex_buf, "0x%08X", (unsigned int)hr);
        log_msg("DuplicateOutput attempt #" + std::to_string(retry_count) +
                " failed (" + std::string(hex_buf) + "). Waiting for DXGI access...");
        Sleep(1000);
    }
    log_msg("DXGI Desktop Duplication successfully attached!");

    DXGI_OUTDUPL_DESC duplDesc;
    dupl->GetDesc(&duplDesc);
    int fullW = (int)duplDesc.ModeDesc.Width;
    int fullH = (int)duplDesc.ModeDesc.Height;
    log_msg("Display Resolution: " + std::to_string(fullW) + "x" + std::to_string(fullH));

    ID3D11Texture2D* stagingTex = nullptr;
    D3D11_TEXTURE2D_DESC stagDesc{};
    stagDesc.Width              = fullW;
    stagDesc.Height             = fullH;
    stagDesc.MipLevels          = 1;
    stagDesc.ArraySize          = 1;
    stagDesc.Format             = DXGI_FORMAT_B8G8R8A8_UNORM;
    stagDesc.SampleDesc.Count   = 1;
    stagDesc.Usage              = D3D11_USAGE_STAGING;
    stagDesc.CPUAccessFlags     = D3D11_CPU_ACCESS_READ;
    d3dDev->CreateTexture2D(&stagDesc, nullptr, &stagingTex);

    // Assetto Corsa Native C++ Telemetry Reader
    ACSharedMemoryReaderCPP acTelemetry;
    acTelemetry.connect();

    // Shared Memory IPC Reader (Connecting all other games, DS4, CS2, Pomodoro)
    IPCReaderCPP ipcReader;
    ipcReader.connect();

    float frameInterval_ms = 1000.0f / target_fps;
    LARGE_INTEGER freq, t0, t1;
    QueryPerformanceFrequency(&freq);

    log_msg("Universal C++ Pipeline Active (" + std::to_string(hw.total_leds) +
            " LEDs, " + std::to_string(pkt.size()) + " byte UDP DNRGB packet @ 60 FPS).");

    uint64_t total_packets = 0;
    auto last_stat_log = std::chrono::steady_clock::now();
    bool flash_state = false;
    auto last_flash_toggle = std::chrono::steady_clock::now();
    auto last_dupl_retry = std::chrono::steady_clock::now();

    DXGI_OUTDUPL_FRAME_INFO frameInfo{};
    IDXGIResource* res = nullptr;

    auto set_led = [&](int physical_idx, uint8_t r, uint8_t g, uint8_t b) {
        if (physical_idx >= 0 && physical_idx < hw.total_leds) {
            int p = 4 + physical_idx * 3;
            pkt[p] = r; pkt[p + 1] = g; pkt[p + 2] = b;
        }
    };

    while (true) {
        QueryPerformanceCounter(&t0);

        // 1. Process Multi-Game & Integration Inputs (Priority Order)
        float ac_rpm = 0.0f;
        bool ac_limiter = false;
        bool ac_active = acTelemetry.get_rpm_pct(ac_rpm, ac_limiter);

        const RoomLightsIPC* ipc = ipcReader.get_data();

        auto now_steady = std::chrono::steady_clock::now();
        if (std::chrono::duration_cast<std::chrono::milliseconds>(now_steady - last_flash_toggle).count() >= 100) {
            flash_state = !flash_state;
            last_flash_toggle = now_steady;
        }

        if (ac_active && ac_rpm > 0.0f) {
            // Priority 1: Assetto Corsa Native Shared Memory
            render_rev_meter(pkt, hw, ac_rpm, ac_limiter, flash_state);
        } else if (ipc && ipc->lightbar_mode == 1 && ipc->rpm_pct > 0.0f) {
            // Priority 2: Universal Sim Racing Telemetry (F1, AMS2, Forza, iRacing) via IPC
            render_rev_meter(pkt, hw, ipc->rpm_pct, ipc->is_limiter != 0, flash_state);
        } else if (ipc && ipc->lightbar_mode == 2 && (ipc->ds4_r + ipc->ds4_g + ipc->ds4_b) > 0) {
            // Priority 3: PlayStation DS4 Controller Lightbar Game Color
            if (hw.seg1_start >= 0 && hw.seg2_start >= 0) {
                for (int i = 0; i < hw.seg1_count; i++) set_led(hw.seg1_start + i, ipc->ds4_r, ipc->ds4_g, ipc->ds4_b);
                for (int i = 0; i < hw.seg2_count; i++) set_led(hw.seg2_start + i, ipc->ds4_r, ipc->ds4_g, ipc->ds4_b);
            } else if (hw.single_start >= 0) {
                for (int i = 0; i < hw.single_count; i++) set_led(hw.single_start + i, ipc->ds4_r, ipc->ds4_g, ipc->ds4_b);
            }
        } else if (ipc && ipc->lightbar_mode == 3) {
            // Priority 4: Custom Lightbar Array (CS2 Flashes, Health Pulses, Bomb timer)
            if (hw.seg1_start >= 0 && hw.seg2_start >= 0) {
                for (int i = 0; i < min(hw.seg1_count, 17); i++) {
                    set_led(hw.seg1_start + i, ipc->seg1_rgb[i*3], ipc->seg1_rgb[i*3+1], ipc->seg1_rgb[i*3+2]);
                }
                for (int i = 0; i < min(hw.seg2_count, 18); i++) {
                    set_led(hw.seg2_start + i, ipc->seg2_rgb[i*3], ipc->seg2_rgb[i*3+1], ipc->seg2_rgb[i*3+2]);
                }
            } else if (hw.single_start >= 0) {
                for (int i = 0; i < min(hw.single_count, 35); i++) {
                    if (i < 17) set_led(hw.single_start + i, ipc->seg1_rgb[i*3], ipc->seg1_rgb[i*3+1], ipc->seg1_rgb[i*3+2]);
                    else        set_led(hw.single_start + i, ipc->seg2_rgb[(i-17)*3], ipc->seg2_rgb[(i-17)*3+1], ipc->seg2_rgb[(i-17)*3+2]);
                }
            }
        } else {
            // Idle / Off on lightbar LEDs
            if (hw.seg1_start >= 0 && hw.seg2_start >= 0) {
                for (int i = 0; i < hw.seg1_count; i++) set_led(hw.seg1_start + i, 0, 0, 0);
                for (int i = 0; i < hw.seg2_count; i++) set_led(hw.seg2_start + i, 0, 0, 0);
            } else if (hw.single_start >= 0) {
                for (int i = 0; i < hw.single_count; i++) set_led(hw.single_start + i, 0, 0, 0);
            }
        }

        // Segment 3 (Pomodoro / Aux)
        if (ipc && hw.seg3_start >= 0) {
            for (int i = 0; i < min(hw.seg3_count, 6); i++) {
                set_led(hw.seg3_start + i, ipc->seg3_rgb[i*3], ipc->seg3_rgb[i*3+1], ipc->seg3_rgb[i*3+2]);
            }
        }

        // 2. Process DXGI Desktop Duplication Screen Capture for Segment 0
        if (!dupl) {
            if (std::chrono::duration_cast<std::chrono::milliseconds>(now_steady - last_dupl_retry).count() >= 500) {
                last_dupl_retry = now_steady;
                output1->DuplicateOutput(d3dDev, &dupl);
            }
            sendto(sock, (const char*)pkt.data(), (int)pkt.size(), 0, (sockaddr*)&dest, sizeof(dest));
            total_packets++;
            goto frame_done;
        }

        res = nullptr;
        memset(&frameInfo, 0, sizeof(frameInfo));
        hr = dupl->AcquireNextFrame(0, &frameInfo, &res);

        if (hr == DXGI_ERROR_WAIT_TIMEOUT) {
            sendto(sock, (const char*)pkt.data(), (int)pkt.size(), 0, (sockaddr*)&dest, sizeof(dest));
            total_packets++;
            goto frame_done;
        }

        if (FAILED(hr)) {
            if (dupl) { dupl->Release(); dupl = nullptr; }
            sendto(sock, (const char*)pkt.data(), (int)pkt.size(), 0, (sockaddr*)&dest, sizeof(dest));
            total_packets++;
            goto frame_done;
        }

        {
            ID3D11Texture2D* acqTex = nullptr;
            res->QueryInterface(__uuidof(ID3D11Texture2D), (void**)&acqTex);

            d3dCtx->CopyResource(stagingTex, acqTex);

            acqTex->Release();
            res->Release();
            dupl->ReleaseFrame();

            D3D11_MAPPED_SUBRESOURCE mapped{};
            if (SUCCEEDED(d3dCtx->Map(stagingTex, 0, D3D11_MAP_READ, 0, &mapped))) {
                const uint8_t* px = (const uint8_t*)mapped.pData;
                int rp            = mapped.RowPitch;

                for (int i = 0; i < hw.seg0_count; i++) {
                    const Zone& z = zones[i];
                    int x1 = max(0, min(fullW - 1, z.x));
                    int y1 = max(0, min(fullH - 1, z.y));
                    int x2 = max(x1 + 1, min(fullW, z.x + z.w));
                    int y2 = max(y1 + 1, min(fullH, z.y + z.h));

                    uint32_t sumR = 0, sumG = 0, sumB = 0, count = 0;

                    for (int y = y1; y < y2; y++) {
                        const uint32_t* row = (const uint32_t*)(px + y * rp);
                        for (int x = x1; x < x2; x += PIXELS_PER_STEP) {
                            uint32_t pixel = row[x];
                            sumB += (pixel & 0xFF);
                            sumG += ((pixel >> 8) & 0xFF);
                            sumR += ((pixel >> 16) & 0xFF);
                            count++;
                        }
                    }

                    if (count > 0) {
                        float r = sumR / (count * 255.0f);
                        float g = sumG / (count * 255.0f);
                        float b = sumB / (count * 255.0f);

                        int target_phys_idx = hw.seg0_start + i;
                        int pkt_idx = 4 + target_phys_idx * 3;
                        if (target_phys_idx >= 0 && target_phys_idx < hw.total_leds) {
                            process_pixel(r, g, b, pkt[pkt_idx], pkt[pkt_idx + 1], pkt[pkt_idx + 2]);
                        }
                    }
                }

                d3dCtx->Unmap(stagingTex, 0);

                // Send non-blocking UDP DNRGB frame covering the entire strip
                int bytesSent = sendto(sock, (const char*)pkt.data(), (int)pkt.size(),
                                       0, (sockaddr*)&dest, sizeof(dest));
                if (bytesSent > 0) {
                    total_packets++;
                }

                // Log stats every 5s
                if (std::chrono::duration_cast<std::chrono::seconds>(now_steady - last_stat_log).count() >= 5) {
                    last_stat_log = now_steady;
                    std::string active_source = "IDLE";
                    float cur_rpm = 0.0f;
                    if (ac_active && ac_rpm > 0.0f) { active_source = "AC_NATIVE"; cur_rpm = ac_rpm; }
                    else if (ipc && ipc->lightbar_mode == 1 && ipc->rpm_pct > 0.0f) { active_source = "SIMRACING_IPC"; cur_rpm = ipc->rpm_pct; }
                    else if (ipc && ipc->lightbar_mode == 2) { active_source = "DS4_LIGHTBAR"; }
                    else if (ipc && ipc->lightbar_mode == 3) { active_source = "CUSTOM_ARRAY"; }

                    log_msg("Universal C++ Stats: Sent " + std::to_string(total_packets) +
                            " packets. ActiveSource=" + active_source +
                            ", RPM=" + std::to_string(cur_rpm * 100.0f) + "%");
                }
            }
        }

frame_done:
        QueryPerformanceCounter(&t1);
        double elapsed_ms = (t1.QuadPart - t0.QuadPart) * 1000.0 / freq.QuadPart;
        double wait_ms    = frameInterval_ms - elapsed_ms;
        if (wait_ms > 2.0) {
            Sleep((DWORD)(wait_ms - 1.5));
        }
        while (true) {
            QueryPerformanceCounter(&t1);
            if ((t1.QuadPart - t0.QuadPart) * 1000.0 / freq.QuadPart >= frameInterval_ms) break;
            YieldProcessor();
        }
    }

    timeEndPeriod(1);
    closesocket(sock);
    WSACleanup();
    return 0;
}
