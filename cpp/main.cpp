/*
 * roomlights_capture.cpp
 *
 * Ultra-low-latency ambient screen capture engine for RoomLights.
 * Optimized based on Prismatik's native architecture:
 *
 *   1. Direct DXGI Desktop Duplication - AcquireNextFrame(timeout=0)
 *   2. Automatic DXGI retry loop on 0x80070005 (Access Denied / Multi-app lock)
 *   3. Caps LED zone count to 109 to match physical Segment 0 (331-byte MTU-safe UDP packet)
 *   4. Direct GPU->CPU staging copy without GenerateMips pipeline flush
 *   5. Prismatik 4-pixel strided accumulation (4x speed boost)
 *   6. Real-time event & UDP transmission log file: d:\RoomLights\roomlights_capture.log
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

static const int    WLED_PORT       = 21324;
static const float  GAMMA           = 2.004f; // Hardware gamma from Prismatik profile
static const float  SATURATION      = 1.2f;   // Vibrant saturation boost
static const int    KEEPALIVE_SEC   = 5;      // WLED realtime timeout
static const float  DEFAULT_FPS     = 60.0f;
static const int    PIXELS_PER_STEP = 4;      // Prismatik 4-pixel strided sampling
static const int    DEFAULT_SEG0_LEDS = 109;  // Physical Segment 0 LED count

struct Zone { int x, y, w, h; };

// File logger
static std::ofstream g_logFile;

static void log_msg(const std::string& msg) {
    auto now = std::chrono::system_clock::now();
    auto time_t_now = std::chrono::system_clock::to_time_t(now);
    struct tm tm_buf;
    localtime_s(&tm_buf, &time_t_now);

    char time_str[32];
    std::strftime(time_str, sizeof(time_str), "%Y-%m-%d %H:%M:%S", &tm_buf);

    std::string formatted = "[" + std::string(time_str) + "] [capture] " + msg + "\n";
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
// Prismatik .ini parser (supports any profile name or full path)
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
    return std::vector<Zone>(MOVIES_PROFILE_ZONES, MOVIES_PROFILE_ZONES + DEFAULT_SEG0_LEDS);
}

// ---------------------------------------------------------------------------
// Hardware Gamma & Saturation
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
// Main
// ---------------------------------------------------------------------------
int main(int argc, char* argv[]) {
    g_logFile.open("roomlights_capture.log", std::ios::out | std::ios::app);

    std::string wled_ip     = "10.103.233.251";
    float target_fps        = DEFAULT_FPS;
    int start_led_offset    = 17;        // Default: Seg 0 starts at LED 17
    std::string profile_req = "Movies.ini";

    if (argc > 1) wled_ip = argv[1];
    if (argc > 2) target_fps = (float)std::atof(argv[2]);
    if (argc > 3) start_led_offset = std::atoi(argv[3]);
    if (argc > 4) profile_req = argv[4];

    if (target_fps <= 0.0f) target_fps = DEFAULT_FPS;
    if (start_led_offset < 0) start_led_offset = 0;

    timeBeginPeriod(1);
    log_msg("=== RoomLights Native Capture Engine Started ===");
    log_msg("Target WLED: " + wled_ip + ":" + std::to_string(WLED_PORT));
    log_msg("Segment Start LED Offset: " + std::to_string(start_led_offset));
    log_msg("Target Rate: " + std::to_string((int)target_fps) + " FPS");

    WSADATA wsa;
    WSAStartup(MAKEWORD(2, 2), &wsa);
    SOCKET sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    u_long nonblock = 1; ioctlsocket(sock, FIONBIO, &nonblock);

    sockaddr_in dest{};
    dest.sin_family = AF_INET;
    dest.sin_port   = htons(WLED_PORT);
    inet_pton(AF_INET, wled_ip.c_str(), &dest.sin_addr);

    // Load requested Prismatik profile (capped to 109 LEDs for physical Segment 0)
    auto zones = load_prismatik_profile(profile_req, DEFAULT_SEG0_LEDS);
    int num_leds = (int)zones.size();

    // Header: [0x04=DNRGB, timeout_sec, start_hi, start_lo, R0, G0, B0, ...]
    std::vector<uint8_t> pkt(4 + num_leds * 3, 0);
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

    // Automatic retry loop for DXGI Desktop Duplication (handles 0x80070005 Access Denied when another app locks DXGI)
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

    float frameInterval_ms = 1000.0f / target_fps;
    LARGE_INTEGER freq, t0, t1;
    QueryPerformanceFrequency(&freq);

    log_msg("UDP Streaming Loop Active (" + std::to_string(num_leds) + " LEDs, " +
            std::to_string(pkt.size()) + " byte DNRGB packets -> offset " +
            std::to_string(start_led_offset) + ").");

    uint64_t total_packets = 0;
    auto last_stat_log = std::chrono::steady_clock::now();

    while (true) {
        QueryPerformanceCounter(&t0);

        DXGI_OUTDUPL_FRAME_INFO frameInfo{};
        IDXGIResource* res = nullptr;

        hr = dupl->AcquireNextFrame(0, &frameInfo, &res);

        if (hr == DXGI_ERROR_WAIT_TIMEOUT) {
            Sleep(1);
            goto frame_done;
        }

        if (FAILED(hr)) {
            if (dupl) { dupl->Release(); dupl = nullptr; }
            Sleep(200);
            hr = output1->DuplicateOutput(d3dDev, &dupl);
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

                for (int i = 0; i < num_leds; i++) {
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
                        process_pixel(r, g, b, pkt[4 + i*3], pkt[4 + i*3 + 1], pkt[4 + i*3 + 2]);
                    }
                }

                d3dCtx->Unmap(stagingTex, 0);

                // Send non-blocking UDP DNRGB frame
                int bytesSent = sendto(sock, (const char*)pkt.data(), (int)pkt.size(),
                                       0, (sockaddr*)&dest, sizeof(dest));
                if (bytesSent > 0) {
                    total_packets++;
                }

                // Log UDP transmission statistics every 5 seconds
                auto now_stat = std::chrono::steady_clock::now();
                if (std::chrono::duration_cast<std::chrono::seconds>(now_stat - last_stat_log).count() >= 5) {
                    last_stat_log = now_stat;
                    log_msg("UDP Stats: Sent " + std::to_string(total_packets) +
                            " total packets. Last LED[0] RGB=(" +
                            std::to_string(pkt[4]) + "," + std::to_string(pkt[5]) + "," + std::to_string(pkt[6]) + ")");
                }
            }
        }

frame_done:
        QueryPerformanceCounter(&t1);
        double elapsed_ms = (t1.QuadPart - t0.QuadPart) * 1000.0 / freq.QuadPart;
        double wait_ms    = frameInterval_ms - elapsed_ms;
        if (wait_ms > 1.5) Sleep((DWORD)(wait_ms - 1.0));
    }

    timeEndPeriod(1);
    closesocket(sock);
    WSACleanup();
    return 0;
}
