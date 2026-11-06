#include "terminal.hpp"
#include <algorithm>
#include <cstring>
#include <fstream>
#include <im2d.h>
#include "yolov8.h"

namespace terminal {
rga_buffer_t rga_source(const Frame& frame);
namespace {
void check_rknn(int code, const char* operation) {
    if (code < 0) throw std::runtime_error(std::string(operation) + " RKNN error " + std::to_string(code));
}
}
struct Detector::Impl {
    std::atomic<const char*>* phase = nullptr;
    void stage(const char* value) { if (phase) phase->store(value); }
    rknn_app_context_t app{};
    rknn_tensor_attr native_input{};
    rknn_tensor_mem* input = nullptr;
    std::vector<rknn_tensor_attr> output_attrs;
    std::vector<std::vector<uint8_t>> buffers;
    std::vector<rknn_output> outputs;
    ~Impl() {
        stage("context_release");
        if (input) rknn_destroy_mem(app.rknn_ctx, input);
        if (app.rknn_ctx) rknn_destroy(app.rknn_ctx);
    }
};
Detector::Detector(const Config& config, int core, std::atomic<const char*>* phase) : impl(std::make_unique<Impl>()) {
    auto& p = *impl;
    p.phase = phase;
    std::ifstream file(config.model, std::ios::binary);
    if (!file) throw std::runtime_error("Cannot open model " + config.model);
    std::vector<char> model((std::istreambuf_iterator<char>(file)), {});
    p.stage("rknn_init");
    check_rknn(rknn_init(&p.app.rknn_ctx, model.data(), model.size(), 0, nullptr), "init");
    check_rknn(rknn_set_core_mask(p.app.rknn_ctx, static_cast<rknn_core_mask>(1 << core)), "core mask");
    check_rknn(rknn_query(p.app.rknn_ctx, RKNN_QUERY_IN_OUT_NUM, &p.app.io_num, sizeof(p.app.io_num)), "IO query");
    if (p.app.io_num.n_input != 1 || p.app.io_num.n_output != 9)
        throw std::runtime_error("Expected Rockchip YOLOv8n with one input and nine DFL outputs");
    check_rknn(rknn_query(p.app.rknn_ctx, RKNN_QUERY_NATIVE_INPUT_ATTR, &p.native_input, sizeof(p.native_input)), "input query");
    if (p.native_input.fmt != RKNN_TENSOR_NHWC || p.native_input.n_dims != 4 ||
        p.native_input.dims[1] != 640 || p.native_input.dims[2] != 640 || p.native_input.dims[3] != 3)
        throw std::runtime_error("Expected NHWC 1x640x640x3 native input");
    p.native_input.type = RKNN_TENSOR_UINT8;
    p.stage("input_allocate");
    p.input = rknn_create_mem(p.app.rknn_ctx, p.native_input.size_with_stride);
    if (!p.input) throw std::runtime_error("Cannot allocate RKNN input memory");
    check_rknn(rknn_set_io_mem(p.app.rknn_ctx, p.input, &p.native_input), "input binding");
    p.output_attrs.resize(9); p.buffers.resize(9); p.outputs.resize(9);
    for (uint32_t i = 0; i < 9; ++i) {
        auto& a = p.output_attrs[i]; a.index = i;
        check_rknn(rknn_query(p.app.rknn_ctx, RKNN_QUERY_OUTPUT_ATTR, &a, sizeof(a)), "output query");
        const uint32_t grid = 80 >> (i / 3);
        const uint32_t channels = i % 3 == 0 ? 64 : i % 3 == 1 ? 80 : 1;
        if (a.fmt != RKNN_TENSOR_NCHW || a.type != RKNN_TENSOR_INT8 ||
            a.qnt_type != RKNN_TENSOR_QNT_AFFINE_ASYMMETRIC || a.n_dims != 4 ||
            a.dims[1] != channels || a.dims[2] != grid || a.dims[3] != grid)
            throw std::runtime_error("Model must have quantized COCO80 nine-output DFL tensors");
        p.buffers[i].resize(a.size);
        p.outputs[i].index = i; p.outputs[i].is_prealloc = 1;
        p.outputs[i].buf = p.buffers[i].data(); p.outputs[i].size = a.size;
    }
    p.app.output_attrs = p.output_attrs.data();
    p.app.is_quant = true; p.app.model_classes = 80;
    p.app.model_width = p.app.model_height = 640; p.app.model_channel = 3;
}
Detector::~Detector() = default;
std::shared_ptr<Result> Detector::infer(const FramePtr& frame, float confidence, float nms) {
    auto& p = *impl;
    const double begin = monotonic();
    int w = frame->av->width, h = frame->av->height;
    float scale = std::min(640.f / w, 640.f / h);
    int scaled_w = int(w * scale) & ~1, scaled_h = int(h * scale) & ~1;
    int left = ((640 - scaled_w) / 2) & ~1, top = ((640 - scaled_h) / 2) & ~1;
    auto src = rga_source(*frame);
    auto dst = wrapbuffer_fd(p.input->fd, 640, 640, RK_FORMAT_RGB_888,
                             int(p.native_input.w_stride ? p.native_input.w_stride : 640), 640);
    p.stage("rga_fill");
    auto ret = imfill(dst, {0, 0, 640, 640}, 0x727272);
    if (ret < 0) throw std::runtime_error(std::string("RGA fill: ") + imStrError(ret));
    p.stage("rga_resize");
    ret = improcess(src, dst, {}, {0, 0, w, h}, {left, top, scaled_w, scaled_h}, {}, IM_SYNC);
    if (ret < 0) throw std::runtime_error(std::string("RGA preprocess: ") + imStrError(ret));
    const double prepared = monotonic();
    p.stage("rknn_run");
    check_rknn(rknn_run(p.app.rknn_ctx, nullptr), "run");
    const double inferred = monotonic();
    p.stage("outputs");
    check_rknn(rknn_outputs_get(p.app.rknn_ctx, 9, p.outputs.data(), nullptr), "outputs");
    object_detect_result_list detections{};
    letterbox_t geometry{};
    geometry.scale = scale; geometry.x_pad = left; geometry.y_pad = top;
    p.stage("postprocess");
    int status = post_process(&p.app, p.outputs.data(), &geometry, confidence, nms, &detections);
    rknn_outputs_release(p.app.rknn_ctx, 9, p.outputs.data());
    check_rknn(status, "postprocess");
    auto result = std::make_shared<Result>();
    result->frame = frame;
    for (int i = 0; i < detections.count; ++i) {
        const auto& d = detections.results[i];
        result->boxes.push_back({d.cls_id, d.prop, float(std::clamp(d.box.left, 0, w)),
            float(std::clamp(d.box.top, 0, h)), float(std::clamp(d.box.right, 0, w)), float(std::clamp(d.box.bottom, 0, h))});
    }
    result->preprocess_ms = (prepared - begin) * 1000;
    result->inference_ms = (inferred - prepared) * 1000;
    result->postprocess_ms = (monotonic() - inferred) * 1000;
    return result;
}
}
