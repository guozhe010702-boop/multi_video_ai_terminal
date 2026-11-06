#include "terminal.hpp"
#include <algorithm>
#include <fstream>
#include <iostream>
#include <iomanip>
#include <sstream>
#include <opencv2/imgproc.hpp>
#include <unistd.h>

namespace terminal {
Runtime::Runtime(Config config, fs::path path, fs::path web)
    : web_root(std::move(web)), config_(std::move(config)), config_path(std::move(path)) {
    labels_ = read_labels(config_.labels);
    for (const auto& c : config_.channels)
        for (const auto& name : c.alarm_classes)
            if (std::find(labels_.begin(), labels_.end(), name) == labels_.end())
                throw std::invalid_argument("Unknown alarm class " + name);
    events_ = std::make_unique<Events>(config_, labels_);
}
Runtime::~Runtime() { stop(); }
Config Runtime::config_copy() const { std::lock_guard<std::mutex> lock(config_mutex); return config_; }
Json Runtime::config_json() const { return config_copy().json; }
void Runtime::start_channel(int id) {
    auto& c = channels.at(id);
    if (c.running) return;
    if (c.decoder.joinable()) c.decoder.join();
    auto cfg = config_copy().channels.at(id);
    if (cfg.source.empty()) throw std::invalid_argument("Video source is empty");
    c.running = true;
    c.decoder = std::thread([this, &c, cfg] {
        Decoder::run(c, cfg, [this] { schedule_cv.notify_all(); },
            [this, cfg](const auto& kind, const auto& message) { events_->push(cfg.id, kind, message); });
    });
}
void Runtime::stop_channel(int id) {
    auto& c = channels.at(id);
    c.running = false;
    {
        std::lock_guard<std::mutex> lock(c.mutex);
        ++c.generation; c.pending.reset(); c.latest.reset(); c.alarm.reset();
        c.state = "stopped"; c.error.clear();
    }
    schedule_cv.notify_all();
    if (c.decoder.joinable()) c.decoder.join();
}
void Runtime::start() {
    // Load every model before opening inputs so a broken model fails startup.
    for (int i = 0; i < kNpuWorkers; ++i) { Detector verify(config_copy(), i); }
    for (int i = 0; i < kNpuWorkers; ++i) workers[i] = std::thread([this, i] { worker(i); });
    compositor = std::thread([this] { compose(); });
    telemetry = std::thread([this] { sample(); });
    auto config = config_copy();
    for (int i = 0; i < kMaxChannels; ++i) if (config.channels[i].enabled) start_channel(i);
}
void Runtime::stop() {
    if (cleaned_up.exchange(true)) return;
    stopping = true;
    std::lock_guard<std::mutex> control(control_mutex);
    for (int i = 0; i < kMaxChannels; ++i) stop_channel(i);
    schedule_cv.notify_all(); preview_cv.notify_all();
    for (auto& thread : workers) if (thread.joinable()) thread.join();
    if (compositor.joinable()) compositor.join();
    if (telemetry.joinable()) telemetry.join();
}
void Runtime::configure(const Json& value) {
    auto next = parse_config(value);
    std::lock_guard<std::mutex> control(control_mutex);
    const auto previous = config_copy();
    // Runtime resources have explicit startup lifetimes. Reject partial changes.
    for (const auto* key : {"model", "labels", "listen", "port", "data_dir", "retention_days", "snapshot_limit_mb"})
        if (next.json.value(key, Json()) != previous.json.value(key, Json()))
            throw std::invalid_argument(std::string(key) + " requires stopping and restarting the service");
    for (const auto& cfg : next.channels)
        for (const auto& name : cfg.alarm_classes)
            if (std::find(labels_.begin(), labels_.end(), name) == labels_.end())
                throw std::invalid_argument("Unknown alarm class " + name);
    atomic_json(config_path, next.json);
    for (int i = 0; i < kMaxChannels; ++i) {
        const auto& a = previous.channels[i]; const auto& b = next.channels[i];
        if (a.source != b.source || a.loop != b.loop || !b.enabled || a.alarm_classes != b.alarm_classes)
            stop_channel(i);
    }
    { std::lock_guard<std::mutex> lock(config_mutex); config_ = std::move(next); }
    auto config = config_copy();
    for (int i = 0; i < kMaxChannels; ++i) if (config.channels[i].enabled) start_channel(i);
}
void Runtime::enable(int id, bool enabled) {
    if (id < 0 || id >= kMaxChannels) throw std::invalid_argument("Channel ID must be 0..7");
    auto config = config_json();
    for (auto& c : config["channels"]) if (c["id"] == id) c["enabled"] = enabled;
    configure(config);
}
void Runtime::worker(int core) {
    worker_phase[core] = "idle";
    std::unique_ptr<Detector> detector;
    while (!stopping) {
        int id = -1;
        FramePtr frame;
        {
            std::unique_lock<std::mutex> schedule(scheduler_mutex);
            for (int offset = 0; offset < kMaxChannels; ++offset) {
                int candidate = (next_channel + offset) % kMaxChannels;
                auto& c = channels[candidate];
                std::lock_guard<std::mutex> lock(c.mutex);
                if (c.running && c.pending && !c.busy) {
                    id = candidate; frame = std::move(c.pending); c.busy = true;
                    next_channel = (candidate + 1) % kMaxChannels;
                    break;
                }
            }
            if (id < 0) { schedule_cv.wait_for(schedule, std::chrono::milliseconds(20)); continue; }
        }
        auto& c = channels[id];
        auto config = config_copy();
        worker_started[core] = monotonic();
        try {
            if (!detector) detector = std::make_unique<Detector>(config, core, &worker_phase[core]);
            auto result = detector->infer(frame, config.confidence, config.nms);
            bool capture = false, recovered = false;
            {
                std::lock_guard<std::mutex> lock(c.mutex);
                if (c.running && c.generation == frame->generation) {
                    recovered = c.state == "inference_error";
                    c.latest = result; ++c.detected;
                    c.latency_ms = (monotonic() - frame->received) * 1000;
                    c.error.clear(); c.state = "running";
                    bool present = std::any_of(result->boxes.begin(), result->boxes.end(), [&](const Box& b) {
                        const auto& names = config.channels[id].alarm_classes;
                        return std::find(names.begin(), names.end(), labels_.at(b.class_id)) != names.end();
                    });
                    capture = c.alarm.update(present, monotonic());
                }
            }
            if (capture) events_->push(id, "target", "Configured target present for at least one second", result);
            if (recovered) events_->push(id, "recovered", "Inference recovered");
        } catch (const std::exception& e) {
            bool report = false;
            {
                std::lock_guard<std::mutex> lock(c.mutex);
                if (c.running && c.generation == frame->generation) {
                    report = c.error != e.what();
                    c.error = e.what(); c.state = "inference_error";
                }
            }
            if (report) events_->push(id, "inference_fault", e.what());
            detector.reset();
            std::this_thread::sleep_for(std::chrono::milliseconds(200));
        }
        worker_started[core] = 0;
        worker_phase[core] = "idle";
        { std::lock_guard<std::mutex> lock(c.mutex); c.busy = false; }
        schedule_cv.notify_all();
    }
}
void Runtime::compose() {
    std::array<cv::Mat, kMaxChannels> tiles;
    std::array<uint64_t, kMaxChannels> sequences{}, generations{};
    auto next = Clock::now();
    while (!stopping) {
        const auto config = config_copy();
        const auto layout = preview_layout(config);
        cv::Mat mosaic(layout.height(), layout.width(), CV_8UC3, cv::Scalar(20, 25, 32));
        double total_fps = 0;
        int active = 0;
        for (int id = 0; id < layout.video_tiles; ++id) {
            std::shared_ptr<Result> result;
            std::string state, error;
            double fps;
            bool alarm;
            {
                auto& c = channels[id];
                std::lock_guard<std::mutex> lock(c.mutex);
                result = c.latest; state = c.state; error = c.error;
                fps = c.detect_fps; alarm = c.alarm.active;
                total_fps += fps; if (c.state == "running") ++active;
            }
            cv::Mat tile(360, 640, CV_8UC3, cv::Scalar(25, 30, 40));
            try {
                if (result && monotonic() - result->frame->received < 1) {
                    if (sequences[id] != result->frame->sequence || generations[id] != result->frame->generation || tiles[id].empty()) {
                        tiles[id] = frame_bgr(*result->frame, 640, 360);
                        draw_boxes(tiles[id], *result, labels_);
                        sequences[id] = result->frame->sequence; generations[id] = result->frame->generation;
                    }
                    tiles[id].copyTo(tile);
                } else {
                    cv::putText(tile, state, {22, 170}, cv::FONT_HERSHEY_SIMPLEX, .8, {150, 155, 170}, 2);
                }
            } catch (const std::exception& e) { error = e.what(); }
            cv::rectangle(tile, {0, 0, 640, 32}, {20, 25, 32}, cv::FILLED);
            auto text = config.channels[id].name + " | AI " + std::to_string(int(fps + .5)) + " FPS | " + state;
            cv::putText(tile, text, {10, 22}, cv::FONT_HERSHEY_SIMPLEX, .5, {225, 230, 240}, 1);
            if (alarm) {
                cv::rectangle(tile, {1, 1, 638, 358}, {0, 80, 255}, 4);
                cv::putText(tile, "TARGET ALERT", {10, 345}, cv::FONT_HERSHEY_SIMPLEX, .6, {0, 80, 255}, 2);
            }
            if (!error.empty()) cv::putText(tile, error.substr(0, 78), {10, 62}, cv::FONT_HERSHEY_SIMPLEX, .4, {70, 100, 255}, 1);
            tile.copyTo(mosaic(cv::Rect((id % layout.columns) * 640, (id / layout.columns) * 360, 640, 360)));
        }
        if (layout.video_tiles == kMaxChannels) {
            auto summary = mosaic(cv::Rect(1280, 720, 640, 360));
            Json metrics;
            double actual_preview;
            { std::lock_guard<std::mutex> lock(metrics_mutex); metrics = resources; actual_preview = mosaic_fps; }
            auto rate = [](double value) {
                std::ostringstream text; text << std::fixed << std::setprecision(1) << value; return text.str();
            };
            auto metric = [&](const char* key, const char* suffix) {
                if (!metrics.contains(key) || !metrics[key].is_number()) return std::string("N/A");
                return std::to_string(int(std::round(metrics[key].get<double>()))) + suffix;
            };
            const std::array<std::string, 6> lines = {"8-CHANNEL AI TERMINAL",
                "Active inputs: " + std::to_string(active) + "/8",
                "Completed AI: " + rate(total_fps) + " FPS",
                "Preview (actual/target): " + rate(actual_preview) + "/" + std::to_string(config.preview_fps) + " FPS",
                "Board CPU: " + metric("cpu_pct", "%") + " | RAM: " + metric("memory_pct", "%"),
                "App RAM: " + metric("rss_mb", " MB") + " | Temp: " + metric("temperature_c", " C")};
            for (size_t index = 0; index < lines.size(); ++index)
                cv::putText(summary, lines[index], {24, 46 + int(index) * 48},
                    cv::FONT_HERSHEY_SIMPLEX, .7, {225, 230, 240}, 1);
        }
        try {
            auto encoded = jpeg(mosaic);
            { std::lock_guard<std::mutex> lock(preview_mutex); preview_jpeg = std::move(encoded); ++preview_serial; }
            preview_cv.notify_all();
        } catch (const std::exception& e) { std::cerr << "Preview: " << e.what() << '\n'; }
        next += std::chrono::microseconds(1000000 / config.preview_fps);
        if (next < Clock::now()) next = Clock::now();
        std::this_thread::sleep_until(next);
    }
}
std::string Runtime::next_jpeg(uint64_t& serial, const std::function<bool()>& connected) {
    std::unique_lock<std::mutex> lock(preview_mutex);
    preview_cv.wait_for(lock, std::chrono::milliseconds(500), [&] { return stopping || serial != preview_serial; });
    if (stopping || !connected() || serial == preview_serial) return {};
    serial = preview_serial;
    return preview_jpeg;
}
void Runtime::sample() {
    std::array<uint64_t, kMaxChannels> decoded{}, detected{};
    uint64_t mosaic_previous = 0, cpu_previous = 0, idle_previous = 0;
    double previous = monotonic();
    while (!stopping) {
        for (int i = 0; i < 10 && !stopping; ++i) std::this_thread::sleep_for(std::chrono::milliseconds(100));
        double now = monotonic(), elapsed = now - previous;
        for (int id = 0; id < kMaxChannels; ++id) {
            auto& c = channels[id]; std::lock_guard<std::mutex> lock(c.mutex);
            c.decode_fps = (c.decoded - decoded[id]) / elapsed;
            c.detect_fps = (c.detected - detected[id]) / elapsed;
            decoded[id] = c.decoded; detected[id] = c.detected;
        }
        Json r = {{"cpu_pct", nullptr}, {"rss_mb", nullptr}, {"memory_pct", nullptr},
                  {"temperature_c", nullptr}, {"npu_pct", {nullptr, nullptr, nullptr}}};
        std::ifstream stat("/proc/stat");
        std::string cpu; uint64_t user, nice, system, idle, iowait, irq, softirq, steal;
        if (stat >> cpu >> user >> nice >> system >> idle >> iowait >> irq >> softirq >> steal) {
            uint64_t total = user + nice + system + idle + iowait + irq + softirq + steal;
            uint64_t idle_sum = idle + iowait;
            if (cpu_previous && total > cpu_previous)
                r["cpu_pct"] = 100. * (1. - double(idle_sum - idle_previous) / (total - cpu_previous));
            cpu_previous = total; idle_previous = idle_sum;
        }
        std::ifstream status("/proc/self/status");
        std::string line;
        while (std::getline(status, line)) {
            if (line.rfind("VmRSS:", 0) == 0) r["rss_mb"] = std::stod(line.substr(6)) / 1024;
            if (line.rfind("Threads:", 0) == 0) r["threads"] = std::stoi(line.substr(8));
        }
        try { r["fd_count"] = std::distance(fs::directory_iterator("/proc/self/fd"), fs::directory_iterator()); }
        catch (const fs::filesystem_error&) { r["fd_count"] = nullptr; }
        std::ifstream memory("/proc/meminfo"); double total_mem = 0, available = 0;
        while (std::getline(memory, line)) {
            if (line.rfind("MemTotal:", 0) == 0) total_mem = std::stod(line.substr(9));
            if (line.rfind("MemAvailable:", 0) == 0) available = std::stod(line.substr(13));
        }
        if (total_mem > 0) r["memory_pct"] = 100 * (1 - available / total_mem);
        std::ifstream temp("/sys/class/thermal/thermal_zone0/temp"); double temperature;
        if (temp >> temperature) r["temperature_c"] = temperature / 1000;
        std::ifstream npu("/sys/kernel/debug/rknpu/load");
        std::getline(npu, line);
        for (int i = 0; i < kNpuWorkers; ++i) {
            auto pos = line.find("Core" + std::to_string(i) + ":");
            if (pos != std::string::npos) try { r["npu_pct"][i] = std::stod(line.substr(pos + 6)); } catch (...) {}
            double began = worker_started[i];
            if (began && now - began > 15) {
                std::cerr << "WATCHDOG: worker " << i << " stage " << worker_phase[i].load() << " blocked for 15 seconds\n";
                std::cerr.flush();
                // Do not destroy a context while its driver call is in progress.
                _Exit(75);
            }
        }
        uint64_t serial;
        { std::lock_guard<std::mutex> lock(preview_mutex); serial = preview_serial; }
        { std::lock_guard<std::mutex> lock(metrics_mutex); resources = r; mosaic_fps = (serial - mosaic_previous) / elapsed; }
        mosaic_previous = serial; previous = now;
    }
}
Json Runtime::status() const {
    auto cfg = config_copy();
    const auto layout = preview_layout(cfg);
    Json output = {{"at", wall_time()}, {"process_id", getpid()}, {"channels", Json::array()}, {"storage", events_->status()},
        {"version", "1.1.0"}, {"max_channels", kMaxChannels},
        {"layout", {{"columns", layout.columns}, {"rows", layout.rows}, {"video_tiles", layout.video_tiles},
                    {"width", layout.width()}, {"height", layout.height()}}}};
    { std::lock_guard<std::mutex> lock(metrics_mutex); output["resources"] = resources; output["mosaic_fps"] = mosaic_fps; }
    output["workers"] = Json::array();
    for (int core = 0; core < kNpuWorkers; ++core) {
        const auto phase = worker_phase[core].load();
        const double began = worker_started[core].load();
        output["workers"].push_back({{"core", core}, {"stage", phase ? phase : "idle"},
            {"busy_seconds", began ? monotonic() - began : 0}});
    }
    for (int id = 0; id < kMaxChannels; ++id) {
        const auto& c = channels[id]; std::lock_guard<std::mutex> lock(c.mutex);
        Json row = {{"id", id}, {"name", cfg.channels[id].name}, {"enabled", cfg.channels[id].enabled}, {"state", c.state}, {"error", c.error},
          {"generation", c.generation}, {"decoded", c.decoded}, {"detected", c.detected}, {"dropped", c.dropped},
          {"decode_fps", c.decode_fps}, {"detect_fps", c.detect_fps}, {"latency_ms", c.latency_ms},
          {"width", c.width}, {"height", c.height}, {"decoder", c.codec}, {"pixel_format", c.pixel_format},
          {"alarm", c.alarm.active}, {"last_frame_age_s", c.last_frame ? Json(monotonic() - c.last_frame) : Json(nullptr)}};
        if (c.latest) {
            row["sequence"] = c.latest->frame->sequence;
            row["preprocess_ms"] = c.latest->preprocess_ms;
            row["inference_ms"] = c.latest->inference_ms;
            row["postprocess_ms"] = c.latest->postprocess_ms;
            row["objects"] = c.latest->boxes.size();
        }
        output["channels"].push_back(row);
    }
    return output;
}
}
