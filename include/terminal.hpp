#pragma once
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <deque>
#include <filesystem>
#include <functional>
#include <memory>
#include <mutex>
#include <set>
#include <string>
#include <thread>
#include <vector>
#include <nlohmann/json.hpp>
#include <opencv2/core.hpp>
extern "C" {
#include <libavutil/frame.h>
}

namespace terminal {
inline constexpr int kMaxChannels = 8;
inline constexpr int kLegacyChannels = 4;
inline constexpr int kNpuWorkers = 3;
using Json = nlohmann::json;
namespace fs = std::filesystem;
using Clock = std::chrono::steady_clock;
inline double monotonic() { return std::chrono::duration<double>(Clock::now().time_since_epoch()).count(); }
inline double wall_time() { return std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count(); }
struct ChannelConfig {
    int id = 0;
    std::string name, source;
    bool enabled = false, loop = true;
    std::vector<std::string> alarm_classes;
};
struct Config {
    Json json;
    std::array<ChannelConfig, kMaxChannels> channels;
    std::string model, labels, listen, data_dir;
    int port = 8090, preview_fps = 15, retention_days = 7;
    uint64_t snapshot_limit_bytes = 2ULL * 1024 * 1024 * 1024;
    float confidence = .35f, nms = .45f;
};
struct PreviewLayout {
    int columns, rows, video_tiles;
    static constexpr int tile_width = 640, tile_height = 360;
    int width() const { return columns * tile_width; }
    int height() const { return rows * tile_height; }
};
inline PreviewLayout preview_layout(const Config& config) {
    for (int id = kLegacyChannels; id < kMaxChannels; ++id)
        if (config.channels[id].enabled || !config.channels[id].source.empty()) return {3, 3, kMaxChannels};
    return {2, 2, kLegacyChannels};
}
Config parse_config(const Json& value);
void atomic_json(const fs::path& path, const Json& value);
std::vector<std::string> read_labels(const fs::path& path);

// Each decoder frame remains referenced until all consumers have released it.
struct Frame {
    AVFrame* av = nullptr;
    uint64_t sequence = 0, generation = 0;
    double received = 0, pts = 0;
    Frame() = default;
    Frame(const Frame&) = delete;
    ~Frame() { av_frame_free(&av); }
};
using FramePtr = std::shared_ptr<Frame>;
struct Box { int class_id; float score; float x1, y1, x2, y2; };
struct Result {
    FramePtr frame;
    std::vector<Box> boxes;
    double preprocess_ms = 0, inference_ms = 0, postprocess_ms = 0;
};
struct AlarmState {
    double present_since = 0, absent_since = 0, last_capture = -1e9;
    bool active = false;
    void reset() { present_since = absent_since = 0; active = false; }
    bool update(bool present, double now) {
        if (present) {
            absent_since = 0;
            if (!present_since) present_since = now;
            if (now - present_since >= 1) active = true;
            if (active && now - last_capture >= 10) { last_capture = now; return true; }
        } else {
            present_since = 0;
            if (!absent_since) absent_since = now;
            if (now - absent_since >= 2) active = false;
        }
        return false;
    }
};
struct Channel {
    mutable std::mutex mutex;
    std::thread decoder;
    std::atomic<bool> running{false};
    uint64_t generation = 0, decoded = 0, detected = 0, dropped = 0, sequence = 0;
    bool busy = false;
    FramePtr pending;
    std::shared_ptr<Result> latest;
    AlarmState alarm;
    std::string state = "stopped", error;
    std::string codec, pixel_format;
    int width = 0, height = 0;
    double decode_fps = 0, detect_fps = 0, latency_ms = 0, last_frame = 0;
};

class Decoder {
public:
    static void run(Channel& channel, const ChannelConfig& config,
        const std::function<void()>& wake,
        const std::function<void(const std::string&, const std::string&)>& event);
};
class Detector {
public:
    Detector(const Config& config, int core, std::atomic<const char*>* phase = nullptr);
    ~Detector();
    std::shared_ptr<Result> infer(const FramePtr& frame, float confidence, float nms);
    Detector(const Detector&) = delete;
private:
    struct Impl;
    std::unique_ptr<Impl> impl;
};
cv::Mat frame_bgr(const Frame& frame, int width, int height);
void draw_boxes(cv::Mat& image, const Result& result, const std::vector<std::string>& labels);
std::string jpeg(const cv::Mat& bgr, int quality = 80);

class Events {
public:
    Events(const Config& config, std::vector<std::string> labels);
    ~Events();
    void push(int channel, std::string kind, std::string message,
              std::shared_ptr<Result> result = {});
    Json list(int limit, int before, int channel);
    Json status();
    fs::path snapshot(const std::string& filename) const;
private:
    struct Impl;
    std::unique_ptr<Impl> impl;
};
class Runtime {
public:
    Runtime(Config config, fs::path config_path, fs::path web_root);
    ~Runtime();
    void start();
    void stop();
    Json status() const;
    Json config_json() const;
    void configure(const Json& value);
    void enable(int id, bool enabled);
    Events& events() { return *events_; }
    std::string next_jpeg(uint64_t& serial, const std::function<bool()>& connected);
    std::atomic<bool> stopping{false};
    fs::path web_root;
private:
    std::atomic<bool> cleaned_up{false};
    mutable std::mutex config_mutex, control_mutex, metrics_mutex, preview_mutex;
    std::mutex scheduler_mutex;
    std::condition_variable schedule_cv, preview_cv;
    Config config_;
    fs::path config_path;
    std::vector<std::string> labels_;
    std::array<Channel, kMaxChannels> channels;
    std::array<std::thread, kNpuWorkers> workers;
    std::array<std::atomic<double>, kNpuWorkers> worker_started{};
    std::array<std::atomic<const char*>, kNpuWorkers> worker_phase{};
    std::thread compositor, telemetry;
    std::unique_ptr<Events> events_;
    size_t next_channel = 0;
    std::string preview_jpeg;
    uint64_t preview_serial = 0;
    double mosaic_fps = 0;
    Json resources;
    Config config_copy() const;
    void start_channel(int id);
    void stop_channel(int id);
    void worker(int core);
    void compose();
    void sample();
};
int serve(Runtime& runtime, const Config& config);
}
