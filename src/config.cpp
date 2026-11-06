#include "terminal.hpp"
#include <fstream>
#include <set>
#include <fcntl.h>
#include <unistd.h>

namespace terminal {
Config parse_config(const Json& j) {
    Config c;
    c.json = j;
    c.listen = j.value("listen", "0.0.0.0");
    c.port = j.value("port", 8090);
    c.model = j.at("model").get<std::string>();
    c.labels = j.at("labels").get<std::string>();
    c.data_dir = j.value("data_dir", "data");
    c.confidence = j.value("confidence", .35f);
    c.nms = j.value("nms", .45f);
    c.preview_fps = j.value("preview_fps", 15);
    c.retention_days = j.value("retention_days", 7);
    int limit_mb = j.value("snapshot_limit_mb", 2048);
    if (c.port < 1024 || c.port > 65535 || c.preview_fps < 1 || c.preview_fps > 30 ||
        !std::isfinite(c.confidence) || c.confidence <= 0 || c.confidence >= 1 ||
        !std::isfinite(c.nms) || c.nms <= 0 || c.nms >= 1 ||
        c.retention_days < 1 || c.retention_days > 365 || limit_mb < 1 || limit_mb > 102400 ||
        c.model.empty() || c.labels.empty() || c.data_dir.empty())
        throw std::invalid_argument("Invalid model, threshold, port, preview or storage setting");
    c.snapshot_limit_bytes = uint64_t(limit_mb) * 1024 * 1024;
    if (!j.at("channels").is_array() ||
        (j["channels"].size() != kLegacyChannels && j["channels"].size() != kMaxChannels))
        throw std::invalid_argument("Provide eight channels, or a legacy four-channel configuration");
    // Expand legacy files without opening additional inputs or changing their sources.
    if (j["channels"].size() == kLegacyChannels)
        for (int id = kLegacyChannels; id < kMaxChannels; ++id)
            c.json["channels"].push_back({{"id", id}, {"name", "Camera " + std::to_string(id + 1)},
                {"source", ""}, {"enabled", false}, {"loop", true}, {"alarm_classes", {"person"}}});
    std::set<int> ids;
    for (const auto& v : c.json["channels"]) {
        ChannelConfig t;
        t.id = v.at("id").get<int>();
        if (t.id < 0 || t.id >= kMaxChannels || !ids.insert(t.id).second)
            throw std::invalid_argument("Channel IDs must be unique and in range 0..7");
        t.name = v.at("name").get<std::string>();
        t.source = v.at("source").get<std::string>();
        t.enabled = v.value("enabled", false);
        t.loop = v.value("loop", true);
        t.alarm_classes = v.value("alarm_classes", std::vector<std::string>{"person"});
        if (t.name.empty() || t.name.size() > 80 || t.source.size() > 2048 ||
            t.source.find_first_of("\r\n") != std::string::npos || (t.enabled && t.source.empty()))
            throw std::invalid_argument("Invalid channel name or source");
        if (t.source.find("://") != std::string::npos && t.source.rfind("rtsp://", 0) != 0)
            throw std::invalid_argument("Sources must be RTSP URLs or local files");
        c.channels[t.id] = std::move(t);
    }
    return c;
}
void atomic_json(const fs::path& path, const Json& value) {
    const auto parent = path.parent_path().empty() ? fs::path(".") : path.parent_path();
    fs::create_directories(parent);
    const std::string temp = path.string() + ".tmp";
    int fd = open(temp.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0600);
    if (fd < 0) throw std::runtime_error("Cannot create configuration temporary file");
    const std::string data = value.dump(2) + "\n";
    size_t done = 0;
    while (done < data.size()) {
        ssize_t n = write(fd, data.data() + done, data.size() - done);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) { close(fd); unlink(temp.c_str()); throw std::runtime_error("Configuration write failed"); }
        done += size_t(n);
    }
    if (fsync(fd) != 0) { close(fd); unlink(temp.c_str()); throw std::runtime_error("Configuration fsync failed"); }
    close(fd);
    if (rename(temp.c_str(), path.c_str()) != 0) { unlink(temp.c_str()); throw std::runtime_error("Configuration rename failed"); }
    fd = open(parent.c_str(), O_RDONLY | O_DIRECTORY);
    if (fd >= 0) { fsync(fd); close(fd); }
}
std::vector<std::string> read_labels(const fs::path& path) {
    std::ifstream input(path);
    if (!input) throw std::runtime_error("Cannot open labels " + path.string());
    std::vector<std::string> labels;
    std::string line;
    while (std::getline(input, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (!line.empty()) labels.push_back(line);
    }
    if (labels.size() != 80 || labels[0] != "person")
        throw std::runtime_error("Expected COCO80 labels beginning with person");
    return labels;
}
}
