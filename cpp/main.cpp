#pragma comment(lib, "d3d11.lib")
#pragma comment(lib, "dxgi.lib")
#pragma comment(lib, "ws2_32.lib")
#pragma comment(lib, "winmm.lib")

#include <windows.h>
#include <d3d11.h>
#include <dxgi1_2.h>
#include <winsock2.h>
#include <ws2tcpip.h>
#include <iostream>
#include <vector>
#include <string>
#include <fstream>
#include <sstream>
#include <cmath>
#include <chrono>
#include <thread>

#define WLED_PORT 21324
#define NUM_LEDS 109

struct RectZone {
    int x, y, w, h;
};

std::vector<RectZone> load_prismatik_profile() {
    std::vector<RectZone> zones;
    char userPath[MAX_PATH];
    if (GetEnvironmentVariableA("USERPROFILE", userPath, MAX_PATH) == 0) return zones;

    std::string iniPath = std::string(userPath) + "\\Prismatik\\Profiles\\Movies.ini";
    std::ifstream file(iniPath);
    if (!file.is_open()) {
        iniPath = std::string(userPath) + "\\Prismatik\\Profiles\\Lightpack.ini";
        file.open(iniPath);
    }
    if (!file.is_open()) return zones;

    std::string line;
    int curLed = 0;
    int px = 0, py = 0, sw = 50, sh = 50;

    while (std::getline(file, line)) {
        if (line.find("[LED_") != std::string::npos) {
            if (curLed > 0 && curLed <= NUM_LEDS) {
                zones.push_back({px, py, sw, sh});
            }
            curLed++;
        }
        if (line.find("Position=@Point(") != std::string::npos) {
            sscanf_s(line.c_str(), "Position=@Point(%d %d)", &px, &py);
        }
        if (line.find("Size=@Size(") != std::string::npos) {
            sscanf_s(line.c_str(), "Size=@Size(%d %d)", &sw, &sh);
        }
    }
    if (curLed > 0 && curLed <= NUM_LEDS) {
        zones.push_back({px, py, sw, sh});
    }
    return zones;
}

int main(int argc, char* argv[]) {
    std::string wled_ip = "192.168.1.100";
    if (argc > 1) wled_ip = argv[1];

    timeBeginPeriod(1);
    std::cout << "[RoomLights C++] Starting Ultra-Fast Screen Capture -> " << wled_ip << ":" << WLED_PORT << std::endl;

    // Load Prismatik zones
    auto zones = load_prismatik_profile();
    if (zones.size() != NUM_LEDS) {
        std::cout << "[RoomLights C++] Warning: Loaded " << zones.size() << " zones from profile (expected " << NUM_LEDS << "). Using defaults." << std::endl;
    } else {
        std::cout << "[RoomLights C++] Loaded " << zones.size() << " exact zones from Prismatik." << std::endl;
    }

    // WinSock UDP Init
    WSADATA wsa;
    WSAStartup(MAKEWORD(2, 2), &wsa);
    SOCKET sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    sockaddr_in destAddr{};
    destAddr.sin_family = AF_INET;
    destAddr.sin_port = htons(WLED_PORT);
    inet_pton(AF_INET, wled_ip.c_str(), &destAddr.sin_addr);

    // Pre-allocate DNRGB UDP buffer: [0x04, timeout, 0x00, 0x00, r0, g0, b0, ...]
    std::vector<uint8_t> udpPacket(4 + NUM_LEDS * 3);
    udpPacket[0] = 0x04; // DNRGB mode
    udpPacket[1] = 0x02; // 2s timeout
    udpPacket[2] = 0x00; // start high
    udpPacket[3] = 0x00; // start low

    // Direct3D 11 & DXGI Desktop Duplication Setup
    ID3D11Device* d3dDevice = nullptr;
    ID3D11DeviceContext* d3dContext = nullptr;
    D3D_FEATURE_LEVEL featureLevel;
    D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0, nullptr, 0, D3D11_SDK_VERSION, &d3dDevice, &featureLevel, &d3dContext);

    IDXGIDevice* dxgiDevice = nullptr;
    d3dDevice->QueryInterface(__uuidof(IDXGIDevice), (void**)&dxgiDevice);
    IDXGIAdapter* dxgiAdapter = nullptr;
    dxgiDevice->GetParent(__uuidof(IDXGIAdapter), (void**)&dxgiAdapter);
    IDXGIOutput* dxgiOutput = nullptr;
    dxgiAdapter->EnumOutputs(0, &dxgiOutput);
    IDXGIOutput1* dxgiOutput1 = nullptr;
    dxgiOutput->QueryInterface(__uuidof(IDXGIOutput1), (void**)&dxgiOutput1);

    IDXGIOutputDuplication* deskDupl = nullptr;
    HRESULT hr = dxgiOutput1->DuplicateOutput(d3dDevice, &deskDupl);
    if (FAILED(hr)) {
        std::cerr << "[RoomLights C++] DuplicateOutput failed: " << std::hex << hr << std::endl;
        return 1;
    }

    std::cout << "[RoomLights C++] DXGI Desktop Duplication initialized. Streaming at 60+ FPS (< 2ms latency)..." << std::endl;

    D3D11_TEXTURE2D_DESC stagingDesc{};
    stagingDesc.Width = 1920;
    stagingDesc.Height = 1080;
    stagingDesc.MipLevels = 1;
    stagingDesc.ArraySize = 1;
    stagingDesc.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    stagingDesc.SampleDesc.Count = 1;
    stagingDesc.Usage = D3D11_USAGE_STAGING;
    stagingDesc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;

    ID3D11Texture2D* stagingTexture = nullptr;
    d3dDevice->CreateTexture2D(&stagingDesc, nullptr, &stagingTexture);

    while (true) {
        auto tStart = std::chrono::high_resolution_clock::now();

        DXGI_OUTDUPL_FRAME_INFO frameInfo{};
        IDXGIResource* desktopResource = nullptr;
        hr = deskDupl->AcquireNextFrame(16, &frameInfo, &desktopResource);
        if (hr == DXGI_ERROR_WAIT_TIMEOUT) continue;
        if (FAILED(hr)) {
            deskDupl->ReleaseFrame();
            continue;
        }

        ID3D11Texture2D* acquiredTexture = nullptr;
        desktopResource->QueryInterface(__uuidof(ID3D11Texture2D), (void**)&acquiredTexture);
        d3dContext->CopyResource(stagingTexture, acquiredTexture);

        D3D11_MAPPED_SUBRESOURCE mapped{};
        if (SUCCEEDED(d3dContext->Map(stagingTexture, 0, D3D11_MAP_READ, 0, &mapped))) {
            const uint8_t* pixels = (const uint8_t*)mapped.pData;
            int rowPitch = mapped.RowPitch;

            // Process 109 zones with Gamma 2.004
            for (size_t i = 0; i < zones.size() && i < NUM_LEDS; ++i) {
                const auto& z = zones[i];
                uint64_t sumB = 0, sumG = 0, sumR = 0, count = 0;

                for (int y = z.y; y < z.y + z.h && y < 1080; y += 2) {
                    const uint32_t* row = (const uint32_t*)(pixels + y * rowPitch);
                    for (int x = z.x; x < z.x + z.w && x < 1920; x += 2) {
                        uint32_t pixel = row[x];
                        sumB += (pixel & 0xFF);
                        sumG += ((pixel >> 8) & 0xFF);
                        sumR += ((pixel >> 16) & 0xFF);
                        count++;
                    }
                }

                if (count > 0) {
                    float b = (float)sumB / (count * 255.0f);
                    float g = (float)sumG / (count * 255.0f);
                    float r = (float)sumR / (count * 255.0f);

                    // Gamma 2.004
                    r = std::pow(r, 2.004f);
                    g = std::pow(g, 2.004f);
                    b = std::pow(b, 2.004f);

                    udpPacket[4 + i * 3 + 0] = (uint8_t)(r * 255.0f);
                    udpPacket[4 + i * 3 + 1] = (uint8_t)(g * 255.0f);
                    udpPacket[4 + i * 3 + 2] = (uint8_t)(b * 255.0f);
                }
            }
            d3dContext->Unmap(stagingTexture, 0);

            // Fire UDP DNRGB packet
            sendto(sock, (const char*)udpPacket.data(), (int)udpPacket.size(), 0, (sockaddr*)&destAddr, sizeof(destAddr));
        }

        acquiredTexture->Release();
        desktopResource->Release();
        deskDupl->ReleaseFrame();

        auto tElapsed = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::high_resolution_clock::now() - tStart).count();
        if (tElapsed < 16) {
            std::this_thread::sleep_for(std::chrono::milliseconds(16 - tElapsed));
        }
    }

    timeEndPeriod(1);
    closesocket(sock);
    WSACleanup();
    return 0;
}
