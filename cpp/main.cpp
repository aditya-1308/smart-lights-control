/*
 * roomlights_capture.cpp
 *
 * 100% Native C++ Ambient Screen Capture & Sim Racing Telemetry Engine.
 *
 * Features:
 *   1. DXGI Desktop Duplication - AcquireNextFrame(timeout=0) with non-blocking error recovery
 *   2. Direct GPU->CPU staging copy (no GenerateMips stalls)
 *   3. Prismatik 4-pixel strided accumulation (Movies.ini profile)
 *   4. Native Windows Shared Memory ("acpmf_physics" & "acpmf_static") Assetto Corsa Telemetry
 *   5. Professional Sim Racing percentage-based shift lights (65% start -> 95% full -> 96% flash)
 *   6. Single 454-byte UDP DNRGB packet for all 150 LEDs at rock-solid 60 FPS
 *   7. High-precision hybrid frame pacing (zero lag spikes, zero CPU starvation)
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

static const int    TOTAL_LEDS        = 150;    // Full physical LED strip
static const int    SEG0_COUNT        = 109;    // Screen ambient LEDs (LEDs 17..125)
static const int    SEG1_COUNT        = 17;     // Left lightbar half (LEDs 0..16)
static const int    SEG2_COUNT        = 18;     // Right lightbar half (LEDs 126..143)

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
// Prismatik Profile Loader
// ---------------------------------------------------------------------------
static std::vector<Zone> load_prismatik_profile(const std::string& requestedProfile, int max_allowed_leds) {
    std::vector<Zone> zones;
    char userPath[MAX_PATH] = {};
    GetEnvironmentVariableA("USERPROFILE", userPath, MAX_PATH);

    std::string profileName = requestedProfile.empty() ? "Movies" : requestedProfile;
    std::string prismatikDir = std::string(userPath) + "\\Prismatik\\Profiles\\";

    std::vector<std::string> candidates;
    if (requestedProfile.find('\\') != std::string::npos || requestedProfile.find('/') != std::string::npos) {
        candidates.push_back(requestedProfile);
    } else {
        candidates.push_back(prismatikDir + profileName);
        if (profileName.find(".ini") == std::string::npos) {
            candidates.push_back(prismatikDir + profileName + ".ini");
        }
        candidates.push_back(prismatikDir + "Movies.ini");
        candidates.push_back(prismatikDir + "Lightpack.ini");
    }

    for (auto& path : candidates) {
        std::ifstream f(path);
        if (!f.is_open()) continue;

        zones.clear();
        int px = 0, py = 0, sw = 50, sh = 50;
        bool inLed = false;
        std::string line;

        while (std::getline(f, line)) {
            if (!line.empty() && line.back() == '\r') line.pop_back();

            if (line.rfind("[LED_", 0) == 0) {
                if (inLed) zones.push_back({px, py, sw, sh});
                inLed = true;
                px = py = 0; sw = sh = 50;
            } else if (inLed) {
                if (line.rfind("Position=@Point(", 0) == 0)
                    sscanf_s(line.c_str(), "Position=@Point(%d %d)", &px, &py);
                else if (line.rfind("Size=@Size(", 0) == 0)
                    sscanf_s(line.c_str(), "Size=@Size(%d %d)", &sw, &sh);
            }
        }
        if (inLed) zones.push_back({px, py, sw, sh});

        if (!zones.empty()) {
            if ((int)zones.size() > max_allowed_leds) {
                log_msg("Parsed " + std::to_string(zones.size()) + " zones from " + path +
                        ", capping to active Segment 0 count (" + std::to_string(max_allowed_leds) + ").");
                zones.resize(max_allowed_leds);
            } else {
                log_msg("Loaded " + std::to_string(zones.size()) + " LED zones from " + path);
            }
            return zones;
        }
    }

    log_msg("Using embedded Movies.ini 109-zone profile fallback.");
    return std::vector<Zone>(MOVIES_PROFILE_ZONES, MOVIES_PROFILE_ZONES + SEG0_COUNT);
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
// Sim Racing Telemetry Shift Lights Renderer (Percentage of Car Redline)
// ---------------------------------------------------------------------------
static void render_rev_meter(std::vector<uint8_t>& pkt, float rpm_pct, bool is_limiter, bool flash_state) {
    // Professional SimHub-standard RPM scaling:
    //   - 0.00 .. 0.65 : OFF (Ambient driving)
    //   - 0.65 .. 0.75 : Green (Outer tips)
    //   - 0.75 .. 0.88 : Yellow (Middle)
    //   - 0.88 .. 0.95 : Red (Center)
    //   - >= 0.96      : Flashing Shift Light / Limiter
    const float REV_START_PCT = 0.65f;
    const float REV_FULL_PCT  = 0.95f;
    const float REV_LIMIT_PCT = 0.96f;

    uint8_t flash_r = 0, flash_g = 100, flash_b = 255; // Flashing Blue on limiter/shift

    if (is_limiter || rpm_pct >= REV_LIMIT_PCT) {
        uint8_t cr = flash_state ? flash_r : 0;
        uint8_t cg = flash_state ? flash_g : 0;
        uint8_t cb = flash_state ? flash_b : 0;

        for (int i = 0; i < SEG1_COUNT; i++) {
            int seg1_idx = 4 + i * 3;
            pkt[seg1_idx] = cr; pkt[seg1_idx + 1] = cg; pkt[seg1_idx + 2] = cb;
        }
        for (int i = 0; i < SEG2_COUNT; i++) {
            int seg2_idx = 4 + (126 + (17 - i)) * 3;
            pkt[seg2_idx] = cr; pkt[seg2_idx + 1] = cg; pkt[seg2_idx + 2] = cb;
        }
        return;
    }

    if (rpm_pct < REV_START_PCT) {
        // Below start threshold: turn off rev lights
        for (int i = 0; i < SEG1_COUNT; i++) {
            int seg1_idx = 4 + i * 3;
            pkt[seg1_idx] = pkt[seg1_idx + 1] = pkt[seg1_idx + 2] = 0;
        }
        for (int i = 0; i < SEG2_COUNT; i++) {
            int seg2_idx = 4 + (126 + (17 - i)) * 3;
            pkt[seg2_idx] = pkt[seg2_idx + 1] = pkt[seg2_idx + 2] = 0;
        }
        return;
    }

    float span = REV_FULL_PCT - REV_START_PCT;
    float norm = (span > 0.0f) ? max(0.0f, min(1.0f, (rpm_pct - REV_START_PCT) / span)) : 0.0f;
    int lit_left  = (int)std::round(norm * (float)SEG1_COUNT);
    int lit_right = (int)std::round(norm * (float)SEG2_COUNT);

    // Left side (Seg 1: LEDs 0..16, 17 LEDs total)
    for (int i = 0; i < SEG1_COUNT; i++) {
        int seg1_idx = 4 + i * 3;
        if (i < lit_left) {
            uint8_t cr = 0, cg = 0, cb = 0;
            if (i < 6)  { cr = 0;   cg = 255; cb = 0; }    // Green outer tip
            else if (i < 13) { cr = 255; cg = 165; cb = 0; } // Yellow middle
            else             { cr = 255; cg = 0;   cb = 0; } // Red center

            pkt[seg1_idx] = cr; pkt[seg1_idx + 1] = cg; pkt[seg1_idx + 2] = cb;
        } else {
            pkt[seg1_idx] = 0;  pkt[seg1_idx + 1] = 0;  pkt[seg1_idx + 2] = 0;
        }
    }

    // Right side (Seg 2: LEDs 126..143, 18 LEDs total)
    for (int i = 0; i < SEG2_COUNT; i++) {
        int seg2_idx = 4 + (126 + (17 - i)) * 3;
        if (i < lit_right) {
            uint8_t cr = 0, cg = 0, cb = 0;
            if (i < 6)  { cr = 0;   cg = 255; cb = 0; }    // Green outer tip
            else if (i < 14) { cr = 255; cg = 165; cb = 0; } // Yellow middle
            else             { cr = 255; cg = 0;   cb = 0; } // Red center

            pkt[seg2_idx] = cr; pkt[seg2_idx + 1] = cg; pkt[seg2_idx + 2] = cb;
        } else {
            pkt[seg2_idx] = 0;  pkt[seg2_idx + 1] = 0;  pkt[seg2_idx + 2] = 0;
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
    int start_led_offset    = 0;         // Send FULL 150-LED strip starting at LED 0!
    std::string profile_req = "Movies.ini";

    if (argc > 1) wled_ip = argv[1];
    if (argc > 2) target_fps = (float)std::atof(argv[2]);
    if (argc > 3) start_led_offset = std::atoi(argv[3]);
    if (argc > 4) profile_req = argv[4];

    if (target_fps <= 0.0f) target_fps = DEFAULT_FPS;
    if (start_led_offset < 0) start_led_offset = 0;

    timeBeginPeriod(1);
    log_msg("=== RoomLights 100% Native C++ Engine Started ===");
    log_msg("Target WLED: " + wled_ip + ":" + std::to_string(WLED_PORT));
    log_msg("Full Physical LED Strip Payload: " + std::to_string(TOTAL_LEDS) + " LEDs starting at offset " + std::to_string(start_led_offset));
    log_msg("Target Rate: " + std::to_string((int)target_fps) + " FPS");

    WSADATA wsa;
    WSAStartup(MAKEWORD(2, 2), &wsa);
    SOCKET sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    u_long nonblock = 1; ioctlsocket(sock, FIONBIO, &nonblock);

    sockaddr_in dest{};
    dest.sin_family = AF_INET;
    dest.sin_port   = htons(WLED_PORT);
    inet_pton(AF_INET, wled_ip.c_str(), &dest.sin_addr);

    // Load Prismatik Movies profile (109 LEDs for Segment 0)
    auto zones = load_prismatik_profile(profile_req, SEG0_COUNT);

    // Pre-allocate FULL 150-LED strip packet: 4 byte header + 150 * 3 = 454 bytes
    // DNRGB Header: [0x04=DNRGB, timeout_sec, start_hi, start_lo, R0, G0, B0, ...]
    std::vector<uint8_t> pkt(4 + TOTAL_LEDS * 3, 0);
    pkt[0] = 0x04;
    pkt[1] = (uint8_t)KEEPALIVE_SEC;
    pkt[2] = (uint8_t)((start_led_offset >> 8) & 0xFF);
    pkt[3] = (uint8_t)(start_led_offset & 0xFF);

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

    float frameInterval_ms = 1000.0f / target_fps;
    LARGE_INTEGER freq, t0, t1;
    QueryPerformanceFrequency(&freq);

    log_msg("100% Native C++ Capture & Telemetry Loop Active (150 LEDs, 454 byte UDP DNRGB packet @ 60 FPS).");

    uint64_t total_packets = 0;
    auto last_stat_log = std::chrono::steady_clock::now();
    bool flash_state = false;
    auto last_flash_toggle = std::chrono::steady_clock::now();
    auto last_dupl_retry = std::chrono::steady_clock::now();

    DXGI_OUTDUPL_FRAME_INFO frameInfo{};
    IDXGIResource* res = nullptr;

    while (true) {
        QueryPerformanceCounter(&t0);

        // 1. Process Assetto Corsa Shared Memory Telemetry natively in C++
        float rpm_pct = 0.0f;
        bool is_limiter = false;
        bool ac_active = acTelemetry.get_rpm_pct(rpm_pct, is_limiter);

        auto now_steady = std::chrono::steady_clock::now();
        if (std::chrono::duration_cast<std::chrono::milliseconds>(now_steady - last_flash_toggle).count() >= 100) {
            flash_state = !flash_state;
            last_flash_toggle = now_steady;
        }

        if (ac_active && rpm_pct > 0.0f) {
            render_rev_meter(pkt, rpm_pct, is_limiter, flash_state);
        } else {
            // Turn off Rev Meter (Seg 1: 0..16 & Seg 2: 126..143) when not racing
            for (int i = 0; i < SEG1_COUNT; i++) {
                int seg1_idx = 4 + i * 3;
                pkt[seg1_idx] = pkt[seg1_idx+1] = pkt[seg1_idx+2] = 0;
            }
            for (int i = 0; i < SEG2_COUNT; i++) {
                int seg2_idx = 4 + (126 + (17 - i)) * 3;
                pkt[seg2_idx] = pkt[seg2_idx+1] = pkt[seg2_idx+2] = 0;
            }
        }

        // 2. Process DXGI Desktop Duplication Screen Capture for Segment 0 (LEDs 17..125)
        if (!dupl) {
            if (std::chrono::duration_cast<std::chrono::milliseconds>(now_steady - last_dupl_retry).count() >= 500) {
                last_dupl_retry = now_steady;
                output1->DuplicateOutput(d3dDev, &dupl);
            }
            // Still transmit full packet with rev meter / ambient keepalive
            sendto(sock, (const char*)pkt.data(), (int)pkt.size(), 0, (sockaddr*)&dest, sizeof(dest));
            total_packets++;
            goto frame_done;
        }

        res = nullptr;
        memset(&frameInfo, 0, sizeof(frameInfo));
        hr = dupl->AcquireNextFrame(0, &frameInfo, &res);

        if (hr == DXGI_ERROR_WAIT_TIMEOUT) {
            // Screen didn't change: re-send packet immediately with 0 delay
            sendto(sock, (const char*)pkt.data(), (int)pkt.size(), 0, (sockaddr*)&dest, sizeof(dest));
            total_packets++;
            goto frame_done;
        }

        if (FAILED(hr)) {
            // Access lost / mode change: non-blocking cleanup
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

                for (int i = 0; i < SEG0_COUNT; i++) {
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

                        // Offset 17 for Segment 0 physical LEDs
                        int target_led_idx = 17 + i;
                        int pkt_idx = 4 + target_led_idx * 3;
                        process_pixel(r, g, b, pkt[pkt_idx], pkt[pkt_idx + 1], pkt[pkt_idx + 2]);
                    }
                }

                d3dCtx->Unmap(stagingTex, 0);

                // Send non-blocking UDP DNRGB frame covering the entire 150-LED strip
                int bytesSent = sendto(sock, (const char*)pkt.data(), (int)pkt.size(),
                                       0, (sockaddr*)&dest, sizeof(dest));
                if (bytesSent > 0) {
                    total_packets++;
                }

                // Log stats every 5s
                if (std::chrono::duration_cast<std::chrono::seconds>(now_steady - last_stat_log).count() >= 5) {
                    last_stat_log = now_steady;
                    log_msg("Native C++ UDP Stats: Sent " + std::to_string(total_packets) +
                            " packets. AC Active=" + (ac_active ? "YES" : "NO") +
                            ", MaxRPM=" + std::to_string(acTelemetry.get_max_rpm()) +
                            ", RPM=" + std::to_string(rpm_pct * 100.0f) + "%");
                }
            }
        }

frame_done:
        // High-precision hybrid frame pacing (sleep for majority, spin for final 1ms)
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
