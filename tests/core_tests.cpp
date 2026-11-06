#include "terminal.hpp"
#include <iostream>
using namespace terminal;
void require(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
int main() {
    AlarmState alarm;
    require(!alarm.update(true, 100), "Alarm must wait for persistence");
    require(!alarm.update(true, 100.5), "Alarm must not fire early");
    require(alarm.update(true, 101), "Alarm must fire at one second");
    require(!alarm.update(true, 102), "Snapshots require cooldown");
    alarm.update(false, 103);
    require(alarm.active, "Short absence must not clear active alarm");
    alarm.update(false, 105);
    require(!alarm.active, "Two seconds absent must clear alarm");
    require(!alarm.update(true, 106), "Reappearance must persist again");
    require(!alarm.update(true, 107), "Cooldown applies across reappearance");
    require(alarm.update(true, 111), "Cooldown expires at ten seconds");
    alarm.reset();
    require(!alarm.active, "Reconnect must clear active alarm");
    require(!alarm.update(true, 112), "Reconnected input must persist again");
    require(!alarm.update(true, 113), "Reconnect must preserve the snapshot cooldown");
    Json config = {{"model", "test.rknn"}, {"labels", "labels.txt"}, {"channels", Json::array()}};
    for (int i = 0; i < 4; ++i) config["channels"].push_back({{"id", i}, {"name", "test"}, {"source", "test.mp4"}});
    auto legacy = parse_config(config);
    require(legacy.channels[3].id == 3 && legacy.json["channels"].size() == 8, "Legacy configuration must expand to eight slots");
    require(!legacy.channels[7].enabled && legacy.channels[7].source.empty(), "Migration must not open extra inputs");
    require(preview_layout(legacy).width() == 1280, "Legacy inputs keep the four-view preview");
    config["channels"][3]["id"] = 0;
    try { parse_config(config); return 1; } catch (const std::invalid_argument&) {}
    config["channels"][3]["id"] = 3;
    config["confidence"] = 2;
    try { parse_config(config); return 1; } catch (const std::invalid_argument&) {}
    config = legacy.json;
    config["channels"][7]["source"] = "rtsp://example.test/camera8";
    config["channels"][7]["enabled"] = true;
    auto eight = parse_config(config);
    require(eight.channels[7].enabled, "Channel eight must be configurable");
    auto layout = preview_layout(eight);
    require(layout.width() == 1920 && layout.height() == 1080 && layout.video_tiles == 8,
        "Eight inputs need eight video tiles and a summary tile");
    config["channels"][7]["id"] = 8;
    try { parse_config(config); return 1; } catch (const std::invalid_argument&) {}
    config["channels"][7]["id"] = 0;
    try { parse_config(config); return 1; } catch (const std::invalid_argument&) {}
    config = eight.json;
    config["channels"].erase(config["channels"].end() - 1);
    try { parse_config(config); return 1; } catch (const std::invalid_argument&) {}
    std::cout << "Alarm persistence, cooldown and config validation passed\n";
}
