#include "terminal.hpp"
#include <fstream>
#include <sqlite3.h>

namespace terminal {
namespace {
struct Statement {
    sqlite3_stmt* value = nullptr;
    Statement(sqlite3* db, const char* sql) {
        if (sqlite3_prepare_v2(db, sql, -1, &value, nullptr) != SQLITE_OK)
            throw std::runtime_error(sqlite3_errmsg(db));
    }
    ~Statement() { sqlite3_finalize(value); }
    void text(int id, const std::string& text) { sqlite3_bind_text(value, id, text.c_str(), -1, SQLITE_TRANSIENT); }
    void execute() {
        if (sqlite3_step(value) != SQLITE_DONE) throw std::runtime_error(sqlite3_errmsg(sqlite3_db_handle(value)));
    }
};
std::string column(sqlite3_stmt* s, int index) {
    const auto* value = sqlite3_column_text(s, index);
    return value ? reinterpret_cast<const char*>(value) : "";
}
}
struct Events::Impl {
    struct Event { int channel; std::string kind, message; double at; std::shared_ptr<Result> result; };
    Config config;
    std::vector<std::string> labels;
    sqlite3* db = nullptr;
    mutable std::mutex mutex, db_mutex;
    std::condition_variable cv;
    std::deque<Event> queue;
    bool stopping = false;
    std::string error;
    uint64_t rejected = 0;
    std::thread writer;
    fs::path folder;
    Impl(const Config& c, std::vector<std::string> names) : config(c), labels(std::move(names)) {
        folder = fs::absolute(fs::path(config.data_dir) / "snapshots");
        fs::create_directories(folder);
        if (sqlite3_open((fs::path(config.data_dir) / "events.sqlite3").c_str(), &db) != SQLITE_OK)
            throw std::runtime_error("Cannot open event database");
        sqlite3_busy_timeout(db, 3000);
        const char* sql = "PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;"
            "CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, at REAL NOT NULL, channel INTEGER NOT NULL,"
            "kind TEXT NOT NULL, message TEXT NOT NULL, snapshot TEXT NOT NULL, details TEXT NOT NULL);"
            "CREATE INDEX IF NOT EXISTS events_at ON events(at);";
        char* message = nullptr;
        if (sqlite3_exec(db, sql, nullptr, nullptr, &message) != SQLITE_OK) {
            std::string e = message ? message : "SQLite initialization failed";
            sqlite3_free(message); sqlite3_close(db); db = nullptr;
            throw std::runtime_error(e);
        }
        // Reconcile interrupted writes before accepting events.
        std::set<std::string> referenced;
        Statement select(db, "SELECT snapshot FROM events WHERE snapshot<>''");
        while (sqlite3_step(select.value) == SQLITE_ROW) referenced.insert(column(select.value, 0));
        for (const auto& item : fs::directory_iterator(folder))
            if (item.is_regular_file() && (item.path().extension() == ".tmp" ||
                (item.path().extension() == ".jpg" && !referenced.count(item.path().filename().string()))))
                fs::remove(item.path());
        writer = std::thread([this] { run(); });
    }
    ~Impl() {
        { std::lock_guard<std::mutex> lock(mutex); stopping = true; }
        cv.notify_all();
        if (writer.joinable()) writer.join();
        if (db) sqlite3_close(db);
    }
    void cleanup() {
        std::lock_guard<std::mutex> lock(db_mutex);
        struct Stored { int id; double at; std::string file; uint64_t bytes; };
        std::vector<Stored> rows;
        uint64_t total = 0;
        {
            Statement s(db, "SELECT id,at,snapshot FROM events ORDER BY id ASC");
            while (sqlite3_step(s.value) == SQLITE_ROW) {
                Stored row{sqlite3_column_int(s.value, 0), sqlite3_column_double(s.value, 1), column(s.value, 2), 0};
                std::error_code ec;
                if (!row.file.empty() && fs::is_regular_file(folder / row.file, ec)) row.bytes = fs::file_size(folder / row.file, ec);
                total += row.bytes; rows.push_back(row);
            }
        }
        double cutoff = wall_time() - config.retention_days * 86400.;
        for (const auto& row : rows) {
            if (row.at >= cutoff && total <= config.snapshot_limit_bytes) continue;
            Statement erase(db, "DELETE FROM events WHERE id=?");
            sqlite3_bind_int(erase.value, 1, row.id); erase.execute();
            if (!row.file.empty()) fs::remove(folder / row.file);
            total -= row.bytes;
        }
    }
    void save(const Event& event) {
        std::string filename;
        Json details = Json::object();
        fs::path temporary, final_path;
        try {
            if (event.result) {
                const auto& result = *event.result;
                auto& f = *result.frame;
                filename = std::to_string(uint64_t(event.at * 1000000)) + "-" + std::to_string(event.channel) + ".jpg";
                final_path = folder / filename; temporary = final_path.string() + ".tmp";
                cv::Mat image = frame_bgr(f, f.av->width, f.av->height);
                draw_boxes(image, result, labels);
                const auto bytes = jpeg(image, 88);
                std::ofstream output(temporary, std::ios::binary);
                output.write(bytes.data(), bytes.size()); output.close();
                if (!output) throw std::runtime_error("Snapshot write failed, check free disk space");
                fs::rename(temporary, final_path);
                details = {{"sequence", f.sequence}, {"generation", f.generation}, {"pts", f.pts}, {"boxes", Json::array()}};
                for (const auto& b : result.boxes) details["boxes"].push_back({{"class", labels.at(b.class_id)},
                    {"score", b.score}, {"xyxy", {b.x1, b.y1, b.x2, b.y2}}});
            }
            std::lock_guard<std::mutex> lock(db_mutex);
            Statement s(db, "INSERT INTO events(at,channel,kind,message,snapshot,details) VALUES(?,?,?,?,?,?)");
            sqlite3_bind_double(s.value, 1, event.at); sqlite3_bind_int(s.value, 2, event.channel);
            s.text(3, event.kind); s.text(4, event.message); s.text(5, filename); s.text(6, details.dump()); s.execute();
        } catch (...) {
            std::error_code ec;
            if (!temporary.empty()) fs::remove(temporary, ec);
            if (!final_path.empty()) fs::remove(final_path, ec);
            throw;
        }
    }
    void run() {
        double last_cleanup = 0;
        while (true) {
            Event event;
            bool found = false;
            {
                std::unique_lock<std::mutex> lock(mutex);
                cv.wait_for(lock, std::chrono::seconds(1), [&] { return stopping || !queue.empty(); });
                if (!queue.empty()) { event = std::move(queue.front()); queue.pop_front(); found = true; }
                else if (stopping) break;
            }
            try {
                if (found) save(event);
                if (monotonic() - last_cleanup > 30) { cleanup(); last_cleanup = monotonic(); }
                if (found) { std::lock_guard<std::mutex> lock(mutex); error.clear(); }
            } catch (const std::exception& e) {
                std::lock_guard<std::mutex> lock(mutex); error = e.what();
            }
        }
    }
};
Events::Events(const Config& c, std::vector<std::string> labels) : impl(std::make_unique<Impl>(c, std::move(labels))) {}
Events::~Events() = default;
void Events::push(int channel, std::string kind, std::string message, std::shared_ptr<Result> result) {
    std::lock_guard<std::mutex> lock(impl->mutex);
    if (impl->queue.size() >= 16) { ++impl->rejected; impl->error = "Event queue full"; return; }
    impl->queue.push_back({channel, std::move(kind), std::move(message), wall_time(), std::move(result)});
    impl->cv.notify_one();
}
Json Events::status() {
    std::lock_guard<std::mutex> lock(impl->mutex);
    return {{"queued", impl->queue.size()}, {"dropped", impl->rejected}, {"error", impl->error}};
}
Json Events::list(int limit, int before, int channel) {
    if (channel < -1 || channel >= kMaxChannels || before < 0)
        throw std::invalid_argument("Invalid event channel or cursor");
    std::lock_guard<std::mutex> lock(impl->db_mutex);
    Statement s(impl->db, "SELECT id,at,channel,kind,message,snapshot,details FROM events "
        "WHERE (?=0 OR id<?) AND (?<0 OR channel=?) ORDER BY id DESC LIMIT ?");
    sqlite3_bind_int(s.value, 1, before); sqlite3_bind_int(s.value, 2, before);
    sqlite3_bind_int(s.value, 3, channel); sqlite3_bind_int(s.value, 4, channel);
    sqlite3_bind_int(s.value, 5, std::clamp(limit, 1, 100));
    Json rows = Json::array();
    while (sqlite3_step(s.value) == SQLITE_ROW) {
        auto filename = column(s.value, 5);
        bool available = !filename.empty() && fs::is_regular_file(impl->folder / filename);
        rows.push_back({{"id", sqlite3_column_int(s.value, 0)}, {"at", sqlite3_column_double(s.value, 1)},
            {"channel", sqlite3_column_int(s.value, 2)}, {"kind", column(s.value, 3)}, {"message", column(s.value, 4)},
            {"snapshot", available ? Json("/snapshots/" + filename) : Json(nullptr)},
            {"details", Json::parse(column(s.value, 6))}});
    }
    return rows;
}
fs::path Events::snapshot(const std::string& filename) const {
    if (filename.empty() || fs::path(filename).filename() != filename || fs::path(filename).extension() != ".jpg")
        throw std::invalid_argument("Invalid snapshot filename");
    return impl->folder / filename;
}
}
