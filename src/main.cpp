#include "terminal.hpp"
#include <csignal>
#include <fstream>
#include <iostream>
#include <fcntl.h>
#include <sys/file.h>
#include <unistd.h>

namespace {
volatile std::sig_atomic_t interrupted = 0;
void signal_handler(int) { interrupted = 1; }
struct ProcessLock {
    int fd;
    ~ProcessLock() { if (fd >= 0) close(fd); }
};
}
int main(int argc, char** argv) {
    using namespace terminal;
    try {
        fs::path path = "config.json";
        bool check = false;
        for (int i = 1; i < argc; ++i) {
            std::string arg = argv[i];
            if (arg == "--config" && i + 1 < argc) path = argv[++i];
            else if (arg == "--check") check = true;
            else { std::cerr << "Usage: multi_video_ai_terminal [--config FILE] [--check]\n"; return 2; }
        }
        path = fs::absolute(path);
        std::ifstream input(path);
        if (!input) throw std::runtime_error("Configuration file not found");
        auto config = parse_config(Json::parse(input));
        fs::current_path(path.parent_path());
        read_labels(config.labels);
        if (check) {
            for (int core = 0; core < 3; ++core) { Detector detector(config, core); }
            std::cout << "Model contract and all three NPU cores verified\n";
            return 0;
        }
        fs::create_directories(config.data_dir);
        ProcessLock lock{open((fs::path(config.data_dir) / "terminal.lock").c_str(), O_CREAT | O_RDWR, 0600)};
        if (lock.fd < 0 || flock(lock.fd, LOCK_EX | LOCK_NB) != 0) throw std::runtime_error("This terminal is already running");
        std::signal(SIGINT, signal_handler); std::signal(SIGTERM, signal_handler); std::signal(SIGPIPE, SIG_IGN);
        Runtime runtime(config, path, "web");
        runtime.start();
        std::thread signals([&] {
            while (!interrupted && !runtime.stopping) std::this_thread::sleep_for(std::chrono::milliseconds(100));
            runtime.stopping = true;
        });
        int code;
        try {
            code = serve(runtime, config);
        } catch (...) {
            runtime.stopping = true;
            signals.join();
            runtime.stop();
            throw;
        }
        runtime.stop();
        signals.join();
        return code;
    } catch (const std::exception& e) { std::cerr << "ERROR: " << e.what() << '\n'; return 1; }
}
