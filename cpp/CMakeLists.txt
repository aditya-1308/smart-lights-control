cmake_minimum_required(VERSION 3.15)
project(RoomLightsCapture CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(RoomLightsCapture main.cpp)

target_link_libraries(RoomLightsCapture
    d3d11
    dxgi
    ws2_32
    winmm
)
