#include "terminal.hpp"
#include <algorithm>
#include <cstring>
#include <sstream>
#include <stdexcept>
#include <fcntl.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>
#include <opencv2/imgproc.hpp>
#include <turbojpeg.h>
#include <im2d.h>
extern "C" {
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/hwcontext.h>
#include <libavutil/hwcontext_drm.h>
}

namespace terminal {
namespace {
std::string av_error(int code) {
    char text[AV_ERROR_MAX_STRING_SIZE];
    av_strerror(code, text, sizeof(text));
    return text;
}
struct Input {
    AVFormatContext* format = nullptr;
    AVCodecContext* codec = nullptr;
    AVBufferRef* device = nullptr;
    AVPacket* packet = av_packet_alloc();
    AVFrame* frame = av_frame_alloc();
    Channel* channel;
    double io_deadline = 0;
    explicit Input(Channel& c) : channel(&c) {}
    ~Input() {
        av_frame_free(&frame);
        av_packet_free(&packet);
        avcodec_free_context(&codec);
        avformat_close_input(&format);
        av_buffer_unref(&device);
    }
    static int interrupt(void* opaque) {
        auto* input = static_cast<Input*>(opaque);
        return !input->channel->running || monotonic() > input->io_deadline;
    }
    int open(const std::string& source) {
        format = avformat_alloc_context();
        format->interrupt_callback = {interrupt, this};
        io_deadline = monotonic() + 5;
        AVDictionary* options = nullptr;
        av_dict_set(&options, "rtsp_transport", "tcp", 0);
        av_dict_set(&options, "timeout", "5000000", 0);
        av_dict_set(&options, "analyzeduration", "1000000", 0);
        av_dict_set(&options, "probesize", "1048576", 0);
        int ret = avformat_open_input(&format, source.c_str(), nullptr, &options);
        av_dict_free(&options);
        if (ret < 0) throw std::runtime_error("Open input: " + av_error(ret));
        ret = avformat_find_stream_info(format, nullptr);
        if (ret < 0) throw std::runtime_error("Stream info: " + av_error(ret));
        int stream = av_find_best_stream(format, AVMEDIA_TYPE_VIDEO, -1, -1, nullptr, 0);
        if (stream < 0) throw std::runtime_error("No video stream");
        auto id = format->streams[stream]->codecpar->codec_id;
        const char* name = id == AV_CODEC_ID_H264 ? "h264_rkmpp" : id == AV_CODEC_ID_HEVC ? "hevc_rkmpp" : nullptr;
        if (!name) throw std::runtime_error("Expected H.264 or H.265 video");
        const AVCodec* decoder = avcodec_find_decoder_by_name(name);
        if (!decoder) throw std::runtime_error("Private FFmpeg is missing MPP decoder");
        codec = avcodec_alloc_context3(decoder);
        avcodec_parameters_to_context(codec, format->streams[stream]->codecpar);
        codec->pkt_timebase = format->streams[stream]->time_base;
        auto type = av_hwdevice_find_type_by_name("rkmpp");
        ret = av_hwdevice_ctx_create(&device, type, nullptr, nullptr, 0);
        if (ret < 0) throw std::runtime_error("MPP device: " + av_error(ret));
        codec->hw_device_ctx = av_buffer_ref(device);
        av_dict_set(&options, "afbc", "off", 0);
        ret = avcodec_open2(codec, decoder, &options);
        av_dict_free(&options);
        if (ret < 0) throw std::runtime_error("MPP decoder: " + av_error(ret));
        {
            std::lock_guard<std::mutex> lock(channel->mutex);
            channel->codec = name;
        }
        return stream;
    }
};
}

void Decoder::run(Channel& c, const ChannelConfig& config,
                  const std::function<void()>& wake,
                  const std::function<void(const std::string&, const std::string&)>& event) {
    const bool local = config.source.rfind("rtsp://", 0) != 0;
    int failures = 0;
    bool fault_reported = false;
    while (c.running) {
        uint64_t generation;
        {
            std::lock_guard<std::mutex> lock(c.mutex);
            generation = ++c.generation;
            c.state = "connecting";
            c.pending.reset();
            c.latest.reset();
            c.alarm.reset();
        }
        try {
            Input input(c);
            int stream = input.open(config.source);
            double base_pts = -1, wall_start = monotonic();
            double previous_pts = -1;
            auto deliver = [&]() {
                if (input.frame->format != AV_PIX_FMT_DRM_PRIME)
                    throw std::runtime_error("MPP did not return DRM_PRIME frames");
                const int64_t stamp = input.frame->best_effort_timestamp;
                double pts = stamp == AV_NOPTS_VALUE ? -1 : stamp * av_q2d(input.format->streams[stream]->time_base);
                if (local) {
                    if (pts < 0) {
                        auto rate = av_guess_frame_rate(input.format, input.format->streams[stream], nullptr);
                        const double fps = rate.num > 0 ? av_q2d(rate) : 30.;
                        pts = previous_pts < 0 ? 0 : previous_pts + 1 / fps;
                    }
                    if (base_pts < 0 || pts < previous_pts) { base_pts = pts; wall_start = monotonic(); }
                    double due = wall_start + pts - base_pts;
                    while (c.running && monotonic() < due)
                        std::this_thread::sleep_for(std::chrono::milliseconds(2));
                    previous_pts = pts;
                }
                if (!c.running) return;
                auto f = std::make_shared<Frame>();
                f->av = av_frame_clone(input.frame);
                if (!f->av) throw std::bad_alloc();
                f->received = monotonic(); f->pts = pts; f->generation = generation;
                {
                    std::lock_guard<std::mutex> lock(c.mutex);
                    if (generation != c.generation || !c.running) return;
                    f->sequence = ++c.sequence;
                    if (c.pending) ++c.dropped;
                    c.pending = f;
                    ++c.decoded;
                    c.width = f->av->width; c.height = f->av->height;
                    c.pixel_format = "DRM_PRIME/NV12";
                    if (c.state != "inference_error") { c.state = "running"; c.error.clear(); }
                    c.last_frame = f->received;
                }
                input.io_deadline = monotonic() + 5;
                failures = 0;
                if (fault_reported) { event("recovered", "Video input recovered"); fault_reported = false; }
                wake();
            };
            bool eof = false;
            while (c.running) {
                int ret = av_read_frame(input.format, input.packet);
                if (ret == AVERROR_EOF && local) {
                    ret = avcodec_send_packet(input.codec, nullptr);
                    if (ret >= 0) while (avcodec_receive_frame(input.codec, input.frame) == 0) deliver();
                    eof = true;
                    break;
                }
                if (ret < 0) throw std::runtime_error("Read video: " + av_error(ret));
                if (input.packet->stream_index == stream) {
                    ret = avcodec_send_packet(input.codec, input.packet);
                    if (ret == AVERROR(EAGAIN)) {
                        while (avcodec_receive_frame(input.codec, input.frame) == 0) deliver();
                        ret = avcodec_send_packet(input.codec, input.packet);
                    }
                    if (ret < 0) throw std::runtime_error("Decode packet: " + av_error(ret));
                    while ((ret = avcodec_receive_frame(input.codec, input.frame)) == 0) deliver();
                    if (ret != AVERROR(EAGAIN) && ret != AVERROR_EOF)
                        throw std::runtime_error("Decode frame: " + av_error(ret));
                }
                av_packet_unref(input.packet);
                if (monotonic() > input.io_deadline) throw std::runtime_error("No new video frame for 5 seconds");
            }
            if (!c.running) break;
            if (eof && config.loop) continue;
            if (eof) { std::lock_guard<std::mutex> lock(c.mutex); c.state = "eof"; c.running = false; break; }
        } catch (const std::exception& e) {
            if (!c.running) break;
            {
                std::lock_guard<std::mutex> lock(c.mutex);
                ++c.generation; c.pending.reset(); c.latest.reset(); c.alarm.reset();
                c.state = "reconnecting"; c.error = e.what();
            }
            if (!fault_reported) { event("video_fault", e.what()); fault_reported = true; }
        }
        int seconds = std::min(10, 1 << std::min(failures++, 4));
        for (int i = 0; i < seconds * 10 && c.running; ++i)
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
}

// DRM descriptors must describe one linear, contiguous NV12 image. Holding
// the AVFrame reference also holds the dma-buf until synchronous RGA finishes.
rga_buffer_t rga_source(const Frame& frame) {
    auto* descriptor = reinterpret_cast<AVDRMFrameDescriptor*>(frame.av->data[0]);
    if (!descriptor || descriptor->nb_layers != 1 || descriptor->layers[0].nb_planes != 2)
        throw std::runtime_error("Unsupported DRM frame layout");
    const auto& layer = descriptor->layers[0];
    const auto& y = layer.planes[0]; const auto& uv = layer.planes[1];
    constexpr uint32_t nv12 = uint32_t('N') | (uint32_t('V') << 8) | (uint32_t('1') << 16) | (uint32_t('2') << 24);
    if (layer.format != nv12 || y.offset != 0 || y.object_index != uv.object_index ||
        y.pitch <= 0 || uv.pitch != y.pitch || uv.offset % y.pitch ||
        descriptor->objects[y.object_index].format_modifier != 0)
        throw std::runtime_error("RGA requires linear NV12 with a contiguous UV plane");
    return wrapbuffer_fd(descriptor->objects[y.object_index].fd, frame.av->width, frame.av->height,
                         RK_FORMAT_YCbCr_420_SP, int(y.pitch), int(uv.offset / y.pitch));
}
cv::Mat frame_bgr(const Frame& frame, int width, int height) {
    struct DmaBuffer {
        int fd = -1;
        void* address = MAP_FAILED;
        size_t capacity = 0;
        ~DmaBuffer() { release(); }
        void release() {
            if (address != MAP_FAILED) munmap(address, capacity);
            if (fd >= 0) close(fd);
            fd = -1; address = MAP_FAILED; capacity = 0;
        }
        void reserve(size_t bytes) {
            if (capacity >= bytes) return;
            release();
            int heap = open("/dev/dma_heap/system-dma32", O_RDWR | O_CLOEXEC);
            if (heap < 0) throw std::runtime_error("DMA32 heap is unavailable");
            struct Allocation { uint64_t len; uint32_t fd, fd_flags; uint64_t heap_flags; } allocation{};
            allocation.len = (bytes + 4095) & ~size_t(4095);
            allocation.fd_flags = O_RDWR | O_CLOEXEC;
            int ret = ioctl(heap, _IOWR('H', 0, Allocation), &allocation);
            close(heap);
            if (ret < 0) throw std::runtime_error("DMA32 allocation failed");
            fd = int(allocation.fd); capacity = allocation.len;
            address = mmap(nullptr, capacity, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
            if (address == MAP_FAILED) throw std::runtime_error("DMA32 mmap failed");
        }
    };
    thread_local DmaBuffer dma;
    dma.reserve(size_t(width) * height * 3);
    cv::Mat output(height, width, CV_8UC3);
    auto src = rga_source(frame);
    auto dst = wrapbuffer_fd(dma.fd, width, height, RK_FORMAT_BGR_888);
    auto ret = improcess(src, dst, {}, {0, 0, frame.av->width, frame.av->height},
                         {0, 0, width, height}, {}, IM_SYNC);
    if (ret < 0) throw std::runtime_error(std::string("RGA preview: ") + imStrError(ret));
    uint64_t sync = 1; // DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ
    if (ioctl(dma.fd, _IOW('b', 0, uint64_t), &sync) < 0) throw std::runtime_error("DMA cache synchronization failed");
    memcpy(output.data, dma.address, size_t(width) * height * 3);
    sync = 5; // DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ
    if (ioctl(dma.fd, _IOW('b', 0, uint64_t), &sync) < 0) throw std::runtime_error("DMA cache synchronization failed");
    return output;
}
void draw_boxes(cv::Mat& image, const Result& result, const std::vector<std::string>& labels) {
    const float sx = float(image.cols) / result.frame->av->width;
    const float sy = float(image.rows) / result.frame->av->height;
    for (const auto& b : result.boxes) {
        cv::Point a(int(b.x1 * sx), int(b.y1 * sy)), z(int(b.x2 * sx), int(b.y2 * sy));
        cv::rectangle(image, a, z, {70, 220, 100}, 2);
        std::string text = labels.at(b.class_id) + " " + std::to_string(int(b.score * 100)) + "%";
        cv::putText(image, text, {a.x, std::max(12, a.y - 4)}, cv::FONT_HERSHEY_SIMPLEX, .45, {70, 220, 100}, 1);
    }
}
std::string jpeg(const cv::Mat& bgr, int quality) {
    struct Encoder {
        tjhandle handle = tjInitCompress();
        unsigned char* output = nullptr;
        unsigned long capacity = 0;
        ~Encoder() { tjFree(output); if (handle) tjDestroy(handle); }
    };
    thread_local Encoder e;
    unsigned long required = tjBufSize(bgr.cols, bgr.rows, TJSAMP_420);
    if (required > e.capacity) { tjFree(e.output); e.output = tjAlloc(required); e.capacity = required; }
    unsigned long size = e.capacity;
    if (!e.handle || !e.output || tjCompress2(e.handle, bgr.data, bgr.cols, bgr.step, bgr.rows,
        TJPF_BGR, &e.output, &size, TJSAMP_420, quality, TJFLAG_FASTDCT | TJFLAG_NOREALLOC) < 0)
        throw std::runtime_error("JPEG compression failed");
    return std::string(reinterpret_cast<char*>(e.output), size);
}
}
