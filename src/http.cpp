#include "terminal.hpp"
#include <fstream>
#include <httplib.h>

namespace terminal {
int serve(Runtime& runtime, const Config& config) {
    httplib::Server server;
    std::atomic<int> streaming{0};
    std::mutex mutations;
    server.new_task_queue = [] { return new httplib::ThreadPool(16); };
    server.set_payload_max_length(64 * 1024);
    server.set_read_timeout(3, 0); server.set_write_timeout(2, 0);
    server.set_keep_alive_max_count(20);
    server.set_exception_handler([](const auto&, auto& response, std::exception_ptr error) {
        std::string message = "Request failed";
        try { if (error) std::rethrow_exception(error); } catch (const std::exception& e) { message = e.what(); }
        response.status = 400;
        response.set_content(Json({{"error", message}}).dump(), "application/json");
    });
    auto json = [](httplib::Response& response, const Json& value) {
        response.set_header("Cache-Control", "no-store");
        response.set_content(value.dump(), "application/json; charset=utf-8");
    };
    server.Get("/api/status", [&](const auto&, auto& res) { json(res, runtime.status()); });
    server.Get("/api/config", [&](const auto&, auto& res) { json(res, runtime.config_json()); });
    server.Put("/api/config", [&](const auto& req, auto& res) {
        std::lock_guard<std::mutex> lock(mutations);
        runtime.configure(Json::parse(req.body)); json(res, runtime.config_json());
    });
    server.Post(R"(/api/channels/([0-7])/(start|stop))", [&](const auto& req, auto& res) {
        std::lock_guard<std::mutex> lock(mutations);
        runtime.enable(std::stoi(req.matches[1]), req.matches[2] == "start"); json(res, runtime.status());
    });
    server.Get("/api/events", [&](const auto& req, auto& res) {
        auto number = [&](const std::string& key, int fallback) { return req.has_param(key) ? std::stoi(req.get_param_value(key)) : fallback; };
        json(res, runtime.events().list(number("limit", 30), number("before", 0), number("channel", -1)));
    });
    server.Get(R"(/snapshots/([0-9]+-[0-7]\.jpg))", [&](const auto& req, auto& res) {
        std::ifstream input(runtime.events().snapshot(req.matches[1]), std::ios::binary);
        if (!input) { res.status = 404; return; }
        res.set_content(std::string(std::istreambuf_iterator<char>(input), {}), "image/jpeg");
    });
    server.Get("/stream/mosaic.mjpg", [&](const auto&, auto& res) {
        if (streaming.fetch_add(1) >= 8) { --streaming; res.status = 503; return; }
        auto serial = std::make_shared<uint64_t>(0);
        res.set_header("Cache-Control", "no-store");
        res.set_chunked_content_provider("multipart/x-mixed-replace; boundary=frame",
            [&, serial](size_t, httplib::DataSink& sink) {
                auto bytes = runtime.next_jpeg(*serial, [&] { return sink.is_writable(); });
                if (runtime.stopping) return false;
                if (bytes.empty()) return sink.is_writable();
                std::string header = "--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + std::to_string(bytes.size()) + "\r\n\r\n";
                return sink.write(header.data(), header.size()) && sink.write(bytes.data(), bytes.size()) && sink.write("\r\n", 2);
            }, [&](bool) { --streaming; });
    });
    if (!server.set_mount_point("/", runtime.web_root.string())) throw std::runtime_error("Web assets are unavailable");
    std::thread stop_watch([&] {
        while (!runtime.stopping) std::this_thread::sleep_for(std::chrono::milliseconds(100));
        server.stop();
    });
    bool result;
    try {
        result = server.listen(config.listen, config.port);
    } catch (...) {
        runtime.stopping = true;
        server.stop();
        stop_watch.join();
        throw;
    }
    runtime.stopping = true;
    stop_watch.join();
    return result ? 0 : 1;
}
}
