# 仓库内容与本地工程

此仓库发布 RK3588 八路视频 AI 分析终端 v1.1.0 的源码快照。项目根目录包含 C++ 程序、网页、构建/部署脚本、测试、配置示例和依赖版本清单。

## 配套资源

| 内容 | 位置 / 准备方式 |
|---|---|
| C++ / 网页 / 测试 | `src/`、`include/`、`web/`、`tests/` |
| 三方头文件与 SQLite | 由 `tools/prepare.py` 准备，版本和 SHA-256 见 `dependencies.lock.json` |
| Rockchip 多媒体 / RKNN SDK | 需要板端已有 SDK；路径通过 CMake 的 `SDK_PREFIX`、`EDGE_ROOT` 指定 |
| YOLOv8n 模型 | 转换代码为 `tools/convert_model.py`；模型契约和 COCO 类别见 `docs/model-manifest.json` 与 `docs/coco80.txt` |
| 运行模型文件 | 需要另行准备 `models/yolov8n/model.rknn`、`classes.txt` 等，仓库不包含二进制模型 |
| 八路演示视频 | `demo_media/general-motion-v1/` 包含来源、作者、许可、编排和校验清单；`python tools/prepare_demo_media.py --download` 可重新准备视频 |
| 实测摘要 | `validation/` 为 H.264/H.265 八路短测摘要，通用场景短测在 `demo_media/general-motion-v1/VALIDATION.json` |

本地的 `.deps/`、`models/`、`build/`、`runtime/`、`data/`、`releases/` 和实际 `config.json` 按 `.gitignore` 排除，避免将模型、SDK、视频、事件记录和机器配置提交到源码仓库。README 与 VALIDATION 中的这些路径描述原开发工程及开发板现场，仓库本身不含这些运行产物。未发布 GitHub Release 或二进制下载附件。

## 构建前提

现有部署流程基于 Ubuntu 20.04 ARM64、RK3588 驱动和已有多媒体 SDK。部分工具默认从原工作区的相邻 `edgevision -yolov8` 工程读取模型输入和依赖；单独克隆仓库后需要按实际环境调整路径。

`tools/prepare.py` 原有流程会从相邻工程复制 YOLO 后处理文件。仓库中的 `vendor/yolo/` 已包含当前验证使用的修改版；准备依赖前请保留这些文件，避免用旧版本覆盖。上传过程保持现有程序和工具源文件不变。

八路实测采用三个独立 NPU 核心和 640×640 输入。短测总吞吐约 130–133 FPS；`passed` 表示该次功能/采样检查通过，不表示八路各 25 FPS 达标，也不代表完成两小时稳定性或标注精度验收。通用素材抽查中的误识别已记录。

## 第三方来源

YOLO 后处理和相关头文件源自 Rockchip 示例，保留原有版权头；Apache 2.0 许可见 `vendor/licenses/rockchip-apache-2.0.txt`。当前修改包括项目使用的后处理适配与可读性注释，原本地来源与哈希保留在 `dependencies.lock.json`。

cpp-httplib、nlohmann/json 与 Ultralytics 的已有许可文件保留在 `vendor/licenses/`。模型和视频分别沿用各自来源的许可；视频逐片署名与修改说明见 `demo_media/general-motion-v1/CREDITS.md`。
