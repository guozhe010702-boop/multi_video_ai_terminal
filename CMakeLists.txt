cmake_minimum_required(VERSION 3.16)
project(multi_video_ai_terminal VERSION 1.1.0 LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type")
set(SDK_PREFIX /userdata/av_dev/ffmpeg_pipeline/runtime CACHE PATH "Private multimedia SDK")
set(EDGE_ROOT /userdata/edgevision-yolov8 CACHE PATH "Existing verified RKNN SDK")
set(PRIVATE_DEPS ${CMAKE_SOURCE_DIR}/.deps CACHE PATH "Private dependencies")
set(ENV{PKG_CONFIG_PATH} "${SDK_PREFIX}/lib/pkgconfig:$ENV{PKG_CONFIG_PATH}")
find_package(PkgConfig REQUIRED)
pkg_check_modules(AV REQUIRED IMPORTED_TARGET libavformat libavcodec libavutil)
find_package(Threads REQUIRED)
add_library(private_sqlite STATIC ${PRIVATE_DEPS}/src/sqlite3.c)
target_include_directories(private_sqlite PUBLIC ${PRIVATE_DEPS}/include)
target_compile_definitions(private_sqlite PRIVATE SQLITE_THREADSAFE=1 SQLITE_OMIT_LOAD_EXTENSION)
set(OPENCV_ROOT ${EDGE_ROOT}/build-deps/root/usr)
add_executable(multi_video_ai_terminal src/main.cpp src/config.cpp src/video.cpp
  src/detector.cpp src/runtime.cpp src/events.cpp src/http.cpp
  ${CMAKE_SOURCE_DIR}/vendor/yolo/postprocess.cc)
target_include_directories(multi_video_ai_terminal PRIVATE include vendor
  vendor/yolo ${PRIVATE_DEPS}/include ${OPENCV_ROOT}/include/opencv4
  ${OPENCV_ROOT}/include
  ${EDGE_ROOT}/upstream/rknn/include ${EDGE_ROOT}/upstream/utils
  ${SDK_PREFIX}/include/rga ${SDK_PREFIX}/include/rockchip)
target_compile_options(multi_video_ai_terminal PRIVATE -Wall -Wextra -Wno-unused-parameter)
target_link_libraries(multi_video_ai_terminal PRIVATE PkgConfig::AV Threads::Threads
  ${EDGE_ROOT}/upstream/rknn/librknnrt.so ${SDK_PREFIX}/lib/librga.so
  ${SDK_PREFIX}/lib/librockchip_mpp.so
  ${OPENCV_ROOT}/lib/aarch64-linux-gnu/libopencv_core.so
  ${OPENCV_ROOT}/lib/aarch64-linux-gnu/libopencv_imgproc.so
  ${OPENCV_ROOT}/lib/aarch64-linux-gnu/libturbojpeg.so
  private_sqlite dl m)
set_target_properties(multi_video_ai_terminal PROPERTIES
  INSTALL_RPATH "$ORIGIN/../lib")
add_executable(model_benchmark tools/model_benchmark.cpp src/config.cpp src/video.cpp src/detector.cpp
  ${CMAKE_SOURCE_DIR}/vendor/yolo/postprocess.cc)
get_target_property(TERMINAL_INCLUDES multi_video_ai_terminal INCLUDE_DIRECTORIES)
get_target_property(TERMINAL_LIBRARIES multi_video_ai_terminal LINK_LIBRARIES)
target_include_directories(model_benchmark PRIVATE ${TERMINAL_INCLUDES})
target_link_libraries(model_benchmark PRIVATE ${TERMINAL_LIBRARIES})
set_target_properties(model_benchmark PROPERTIES INSTALL_RPATH "$ORIGIN/../lib")
install(TARGETS multi_video_ai_terminal RUNTIME DESTINATION bin)
install(TARGETS model_benchmark RUNTIME DESTINATION bin)
install(DIRECTORY web DESTINATION .)
install(FILES config.example.json DESTINATION .)
install(FILES README.md VALIDATION.md DESTINATION .)
install(PROGRAMS tools/control.py tools/hdmi.py tools/hdmi_health.py tools/x11_preview.py tools/power_monitor.py tools/sample.py DESTINATION tools)
include(CTest)
if(BUILD_TESTING)
  add_library(test_faults SHARED tests/faults.c)
  target_link_libraries(test_faults PRIVATE dl)
  add_executable(core_tests tests/core_tests.cpp src/config.cpp)
  target_include_directories(core_tests PRIVATE include vendor ${PRIVATE_DEPS}/include
    ${OPENCV_ROOT}/include/opencv4 ${SDK_PREFIX}/include)
  target_link_libraries(core_tests PRIVATE PkgConfig::AV Threads::Threads)
  add_test(NAME core_tests COMMAND core_tests)
endif()
