/*
 * roomlights_capture.cpp
 *
 * Ultra-low-latency ambient screen capture engine for RoomLights.
 * Embedded with exact 109-zone Prismatik Movies.ini calibration.
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

static const int    NUM_LEDS        = 109;
static const int    WLED_PORT       = 21324;
static const int    MIP_LEVEL       = 3;   // /8 downscale (matches Prismatik DownscaleMipLevel=3)
static const float  GAMMA           = 2.004f; // Hardware gamma from Movies.ini
static const float  SATURATION      = 1.3f;
static const int    KEEPALIVE_SEC   = 5;   // WLED realtime timeout
static const float  DEFAULT_FPS     = 60.0f;

struct Zone { int x, y, w, h; };

// ---------------------------------------------------------------------------
// Native 109-LED zone layout extracted from Prismatik Movies.ini profile
// ---------------------------------------------------------------------------
static const Zone MOVIES_PROFILE_ZONES[109] = {
    {986, 813, 52, 116},
    {1038, 813, 52, 116},
    {1090, 813, 52, 116},
    {1142, 813, 52, 116},
    {1194, 813, 52, 116},
    {1246, 813, 52, 116},
    {1298, 813, 52, 116},
    {1350, 813, 52, 116},
    {1402, 813, 52, 116},
    {1454, 813, 52, 116},
    {1506, 813, 52, 116},
    {1558, 813, 52, 116},
    {1610, 813, 52, 116},
    {1662, 813, 52, 116},
    {1714, 813, 52, 116},
    {1766, 813, 52, 116},
    {1818, 813, 51, 116},
    {1869, 813, 51, 116},
    {1632, 886, 288, 43},
    {1632, 843, 288, 43},
    {1632, 800, 288, 43},
    {1632, 757, 288, 43},
    {1632, 714, 288, 43},
    {1632, 671, 288, 43},
    {1632, 628, 288, 43},
    {1632, 585, 288, 43},
    {1632, 542, 288, 43},
    {1632, 499, 288, 43},
    {1632, 456, 288, 43},
    {1632, 412, 288, 44},
    {1632, 368, 288, 44},
    {1632, 324, 288, 44},
    {1632, 280, 288, 44},
    {1632, 237, 288, 43},
    {1632, 194, 288, 43},
    {1632, 151, 288, 43},
    {1867, 151, 53, 116},
    {1814, 151, 53, 116},
    {1761, 151, 53, 116},
    {1708, 151, 53, 116},
    {1655, 151, 53, 116},
    {1602, 151, 53, 116},
    {1549, 151, 53, 116},
    {1496, 151, 53, 116},
    {1443, 151, 53, 116},
    {1390, 151, 53, 116},
    {1337, 151, 53, 116},
    {1284, 151, 53, 116},
    {1230, 151, 54, 116},
    {1177, 151, 53, 116},
    {1124, 151, 53, 116},
    {1071, 151, 53, 116},
    {1018, 151, 53, 116},
    {965, 151, 53, 116},
    {912, 151, 53, 116},
    {859, 151, 53, 116},
    {806, 151, 53, 116},
    {753, 151, 53, 116},
    {700, 151, 53, 116},
    {647, 151, 53, 116},
    {594, 151, 53, 116},
    {541, 151, 53, 116},
    {488, 151, 53, 116},
    {435, 151, 53, 116},
    {382, 151, 53, 116},
    {329, 151, 53, 116},
    {276, 151, 53, 116},
    {223, 151, 53, 116},
    {170, 151, 53, 116},
    {117, 151, 53, 116},
    {64, 151, 53, 116},
    {0, 151, 64, 116},
    {0, 151, 288, 43},
    {0, 194, 288, 43},
    {0, 237, 288, 43},
    {0, 280, 288, 44},
    {0, 324, 288, 44},
    {0, 368, 288, 44},
    {0, 412, 288, 44},
    {0, 456, 288, 43},
    {0, 499, 288, 43},
    {0, 542, 288, 43},
    {0, 585, 288, 43},
    {0, 628, 288, 43},
    {0, 671, 288, 43},
    {0, 714, 288, 43},
    {0, 757, 288, 43},
    {0, 800, 288, 43},
    {0, 843, 288, 43},
    {0, 886, 288, 43},
    {0, 813, 52, 116},
    {52, 813, 52, 116},
    {104, 813, 52, 116},
    {156, 813, 52, 116},
    {208, 813, 52, 116},
    {260, 813, 52, 116},
    {312, 813, 52, 116},
    {364, 813, 52, 116},
    {416, 813, 52, 116},
    {468, 813, 52, 116},
    {520, 813, 52, 116},
    {572, 813, 52, 116},
    {624, 813, 52, 116},
    {676, 813, 52, 116},
    {728, 813, 52, 116},
    {780, 813, 52, 116},
    {832, 813, 52, 116},
    {884, 813, 51, 116},
    {935, 813, 51, 116}
};

// ---------------------------------------------------------------------------
// Prismatik .ini parser
// ---------------------------------------------------------------------------
static std::vector<Zone> load_prismatik_profile() {
    std::vector<Zone> zones;
    char userPath[MAX_PATH] = {};
    GetEnvironmentVariableA("USERPROFILE", userPath, MAX_PATH);

    std::string profileName = "Movies";
    std::string mainConf = std::string(userPath) + "\\Prismatik\\main.conf";
    {
        std::ifstream f(mainConf);
        std::string line;
        while (std::getline(f, line)) {
            if (line.rfind("ProfileLast=", 0) == 0) {
                profileName = line.substr(12);
                if (!profileName.empty() && profileName.back() == '\r')
                    profileName.pop_back();
                break;
            }
        }
    }

    std::vector<std::string> candidates = {
        std::string(userPath) + "\\Prismatik\\Profiles\\" + profileName + ".ini",
        std::string(userPath) + "\\Prismatik\\Profiles\\Movies.ini",
        std::string(userPath) + "\\Prismatik\\Profiles\\Lightpack.ini",
    };

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

        if ((int)zones.size() == NUM_LEDS) {
            std::cout << "[capture] Successfully loaded " << NUM_LEDS
                      << " exact zones from " << path << "\n";
            return zones;
        }
    }

    std::cout << "[capture] Loaded native Movies.ini 109-zone calibration profile.\n";
    return std::vector<Zone>(MOVIES_PROFILE_ZONES, MOVIES_PROFILE_ZONES + NUM_LEDS);
}

// ---------------------------------------------------------------------------
// Gamma + saturation processing
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
    std::string wled_ip = "10.103.233.251";
    float target_fps    = DEFAULT_FPS;
    if (argc > 1) wled_ip = argv[1];
    if (argc > 2) target_fps = (float)std::atof(argv[2]);
    if (target_fps <= 0.0f) target_fps = DEFAULT_FPS;

    timeBeginPeriod(1);
    std::cout << "[capture] RoomLights Capture Engine (Prismatik Movies.ini calibrated) -> "
              << wled_ip << ":" << WLED_PORT
              << " @ " << (int)target_fps << " FPS\n";

    WSADATA wsa;
    WSAStartup(MAKEWORD(2, 2), &wsa);
    SOCKET sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    u_long nonblock = 1; ioctlsocket(sock, FIONBIO, &nonblock);

    sockaddr_in dest{};
    dest.sin_family = AF_INET;
    dest.sin_port   = htons(WLED_PORT);
    inet_pton(AF_INET, wled_ip.c_str(), &dest.sin_addr);

    // DNRGB packet: [0x04=DNRGB, timeout_sec, start_hi, start_lo, R0,G0,B0, ...]
    // Physical Segment 0 starts at LED 17 (0x0011)
    std::vector<uint8_t> pkt(4 + NUM_LEDS * 3, 0);
    pkt[0] = 0x04;
    pkt[1] = (uint8_t)KEEPALIVE_SEC;
    pkt[2] = 0x00;
    pkt[3] = 17; // LED 17 start offset

    ID3D11Device*        d3dDev  = nullptr;
    ID3D11DeviceContext* d3dCtx  = nullptr;
    D3D_FEATURE_LEVEL    fl;
    HRESULT hr = D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr,
                                   0, nullptr, 0, D3D11_SDK_VERSION,
                                   &d3dDev, &fl, &d3dCtx);
    if (FAILED(hr)) {
        std::cerr << "[capture] D3D11CreateDevice failed: 0x" << std::hex << hr << "\n";
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
    hr = output1->DuplicateOutput(d3dDev, &dupl);
    if (FAILED(hr)) {
        std::cerr << "[capture] DuplicateOutput failed: 0x" << std::hex << hr << "\n";
        return 1;
    }

    DXGI_OUTDUPL_DESC duplDesc;
    dupl->GetDesc(&duplDesc);
    int fullW = (int)duplDesc.ModeDesc.Width;
    int fullH = (int)duplDesc.ModeDesc.Height;
    std::cout << "[capture] Desktop resolution: " << fullW << "x" << fullH << "\n";

    int mipW = fullW >> MIP_LEVEL;
    int mipH = fullH >> MIP_LEVEL;
    if (mipW < 1) mipW = 1;
    if (mipH < 1) mipH = 1;

    auto zones = load_prismatik_profile();

    float scaleX = (float)mipW / (float)fullW;
    float scaleY = (float)mipH / (float)fullH;
    std::vector<Zone> mipZones(NUM_LEDS);
    for (int i = 0; i < NUM_LEDS; i++) {
        mipZones[i].x = (int)(zones[i].x * scaleX);
        mipZones[i].y = (int)(zones[i].y * scaleY);
        mipZones[i].w = std::max(1, (int)(zones[i].w * scaleX));
        mipZones[i].h = std::max(1, (int)(zones[i].h * scaleY));
    }

    ID3D11Texture2D* mipStaging = nullptr;
    D3D11_TEXTURE2D_DESC stagDesc{};
    stagDesc.Width              = mipW;
    stagDesc.Height             = mipH;
    stagDesc.MipLevels          = 1;
    stagDesc.ArraySize          = 1;
    stagDesc.Format             = DXGI_FORMAT_B8G8R8A8_UNORM;
    stagDesc.SampleDesc.Count   = 1;
    stagDesc.Usage              = D3D11_USAGE_STAGING;
    stagDesc.CPUAccessFlags     = D3D11_CPU_ACCESS_READ;
    d3dDev->CreateTexture2D(&stagDesc, nullptr, &mipStaging);

    ID3D11Texture2D* mipGenTex = nullptr;
    D3D11_TEXTURE2D_DESC mipDesc{};
    mipDesc.Width              = fullW;
    mipDesc.Height             = fullH;
    mipDesc.MipLevels          = 0;
    mipDesc.ArraySize          = 1;
    mipDesc.Format             = DXGI_FORMAT_B8G8R8A8_UNORM;
    mipDesc.SampleDesc.Count   = 1;
    mipDesc.Usage              = D3D11_USAGE_DEFAULT;
    mipDesc.BindFlags          = D3D11_BIND_SHADER_RESOURCE | D3D11_BIND_RENDER_TARGET;
    mipDesc.MiscFlags          = D3D11_RESOURCE_MISC_GENERATE_MIPS;
    d3dDev->CreateTexture2D(&mipDesc, nullptr, &mipGenTex);

    ID3D11ShaderResourceView* mipSRV = nullptr;
    d3dDev->CreateShaderResourceView(mipGenTex, nullptr, &mipSRV);

    float frameInterval_ms = 1000.0f / target_fps;
    LARGE_INTEGER freq, t0, t1;
    QueryPerformanceFrequency(&freq);

    std::cout << "[capture] Running Prismatik Movies.ini profile ambient capture.\n";

    while (true) {
        QueryPerformanceCounter(&t0);

        DXGI_OUTDUPL_FRAME_INFO frameInfo{};
        IDXGIResource* res = nullptr;

        hr = dupl->AcquireNextFrame(0, &frameInfo, &res);

        if (hr == DXGI_ERROR_WAIT_TIMEOUT) {
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

            d3dCtx->CopySubresourceRegion(mipGenTex, 0, 0, 0, 0, acqTex, 0, nullptr);
            d3dCtx->GenerateMips(mipSRV);

            UINT srcSubresource = D3D11CalcSubresource(MIP_LEVEL, 0,
                                    (UINT)(std::log2(std::max(fullW, fullH))) + 1);
            d3dCtx->CopySubresourceRegion(mipStaging, 0, 0, 0, 0,
                                          mipGenTex, srcSubresource, nullptr);

            acqTex->Release();
            res->Release();
            dupl->ReleaseFrame();

            D3D11_MAPPED_SUBRESOURCE mapped{};
            if (SUCCEEDED(d3dCtx->Map(mipStaging, 0, D3D11_MAP_READ, 0, &mapped))) {
                const uint8_t* px  = (const uint8_t*)mapped.pData;
                int            rp  = mapped.RowPitch;

                for (int i = 0; i < NUM_LEDS; i++) {
                    const Zone& z = mipZones[i];
                    int x1 = z.x, y1 = z.y;
                    int x2 = std::min(x1 + z.w, mipW);
                    int y2 = std::min(y1 + z.h, mipH);
                    if (x1 >= mipW || y1 >= mipH || x1 >= x2 || y1 >= y2) {
                        pkt[4 + i*3] = pkt[4 + i*3 + 1] = pkt[4 + i*3 + 2] = 0;
                        continue;
                    }

                    uint32_t sumR = 0, sumG = 0, sumB = 0, count = 0;
                    for (int y = y1; y < y2; y++) {
                        const uint32_t* row = (const uint32_t*)(px + y * rp);
                        for (int x = x1; x < x2; x++) {
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
                        process_pixel(r, g, b, pkt[4+i*3], pkt[4+i*3+1], pkt[4+i*3+2]);
                    }
                }

                d3dCtx->Unmap(mipStaging, 0);

                sendto(sock, (const char*)pkt.data(), (int)pkt.size(),
                       0, (sockaddr*)&dest, sizeof(dest));
            }
        }

frame_done:
        QueryPerformanceCounter(&t1);
        double elapsed_ms = (t1.QuadPart - t0.QuadPart) * 1000.0 / freq.QuadPart;
        double wait_ms    = frameInterval_ms - elapsed_ms;
        if (wait_ms > 1.5) Sleep((DWORD)(wait_ms - 1.0));
        do {
            QueryPerformanceCounter(&t1);
        } while (((t1.QuadPart - t0.QuadPart) * 1000.0 / freq.QuadPart) < frameInterval_ms);
    }

    timeEndPeriod(1);
    closesocket(sock);
    WSACleanup();
    return 0;
}
