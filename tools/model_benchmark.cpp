#include "terminal.hpp"
#include <fstream>
#include <iostream>
using namespace terminal;
int main(int argc, char** argv) {
    try {
        if (argc != 5) throw std::invalid_argument("Usage: model_benchmark CONFIG VIDEO WORKERS SECONDS");
        fs::path path = fs::absolute(argv[1]);
        std::ifstream input(path);
        Config config = parse_config(Json::parse(input));
        fs::path video = fs::absolute(argv[2]);
        int count = std::stoi(argv[3]), seconds = std::stoi(argv[4]);
        if ((count != 1 && count != 3) || seconds < 1 || seconds > 300) throw std::invalid_argument("Workers must be 1 or 3 and duration 1..300");
        fs::current_path(path.parent_path());
        Channel channel;
        ChannelConfig source; source.source = video.string(); source.loop = true;
        channel.running = true;
        std::thread decoder([&] { Decoder::run(channel, source, [] {}, [](const auto&, const auto&) {}); });
        FramePtr frame;
        double deadline = monotonic() + 10;
        while (!frame && monotonic() < deadline) {
            { std::lock_guard<std::mutex> lock(channel.mutex); frame = channel.pending; }
            if (!frame) std::this_thread::sleep_for(std::chrono::milliseconds(5));
        }
        channel.running = false;
        decoder.join();
        if (!frame) throw std::runtime_error("No hardware-decoded test frame");
        std::array<uint64_t, 3> completed{};
        std::array<std::thread, 3> threads;
        std::atomic<int> ready{0};
        std::atomic<bool> go{false};
        std::mutex mutex;
        Json errors = Json::array(), detections = Json::array();
        for (int core = 0; core < count; ++core) threads[core] = std::thread([&,core] {
            try {
                Detector detector(config, core);
                auto result = detector.infer(frame, config.confidence, config.nms);
                {
                    std::lock_guard<std::mutex> lock(mutex);
                    if (core == 0) for (const auto& b : result->boxes)
                        detections.push_back({{"class_id", b.class_id}, {"score", b.score}, {"xyxy", {b.x1,b.y1,b.x2,b.y2}}});
                }
                ++ready;
                while (!go) std::this_thread::sleep_for(std::chrono::milliseconds(1));
                const double end = monotonic() + seconds;
                do { detector.infer(frame, config.confidence, config.nms); ++completed[core]; } while (monotonic() < end);
            } catch (const std::exception& e) { std::lock_guard<std::mutex> lock(mutex); errors.push_back(e.what()); ++ready; }
        });
        while (ready < count) std::this_thread::sleep_for(std::chrono::milliseconds(1));
        const double begin = monotonic(); go = true;
        for (auto& thread : threads) if (thread.joinable()) thread.join();
        const double elapsed = monotonic() - begin;
        uint64_t total = 0; for (auto n : completed) total += n;
        std::cout << Json({{"kind", "isolated_model_benchmark"}, {"workers", count}, {"seconds", elapsed},
            {"iterations", total}, {"iterations_per_second", total/elapsed}, {"errors", errors},
            {"first_frame_detections", detections}}).dump(2) << '\n';
        return errors.empty() ? 0 : 1;
    } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
