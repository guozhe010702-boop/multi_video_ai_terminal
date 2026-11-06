# RK3588 多路视频 AI 分析终端

源码仓库：[guozhe010702-boop/multi_video_ai_terminal](https://github.com/guozhe010702-boop/multi_video_ai_terminal)。首次克隆请先阅读 [仓库内容与构建前提](docs/REPOSITORY.md)：模型、SDK、视频及本机运行数据需另行准备。

v1.1.0 在 EVM3588 Ubuntu 20.04 ARM64 上支持八路 H.264/H.265 RTSP 或本地视频，支持混合输入。检测沿用 YOLOv8n INT8、640×640 和 COCO80。网页与 HDMI 共享预览、告警截图和历史事件。

八路模式先测实际检测帧率，不将四路每路 25 FPS 的历史结果当作八路保证。有效检测计数在 NPU 推理和后处理成功、连接代次校验通过后增加。性能记录和原始数据位于 `VALIDATION.md` 与 `data/validation/`，版本化交付包位于 `releases/`。

## 通用场景连续视频素材

`general-motion-v1` 已补充 9 段真实视频，组成街道、交通、室内、厨房、猫、狗、鸟和篮球八路不同内容，合计约 11 分 47 秒。输出为 1080p30，四路 H.264 与四路 H.265；原始分辨率和许可逐片记录，素材归一化不代表原生规格。

当前板端已切换至这套素材，旧测试片保留。使用说明、来源和校验清单见 [demo_media/general-motion-v1/README.md](demo_media/general-motion-v1/README.md)。电脑准备命令为 `python tools/prepare_demo_media.py --download`，板端切换工具为 `tools/use_demo_media.py`，切换前自动备份配置。本包用于演示和链路检查，没有人工标注的精度结论。

## 开发板运行

当前部署目录为 `/userdata/multi_video_ai_terminal/runtime`。当前 ADB 部署的配置与进程状态文件由 root 持有，以下命令在开发板 Linux 的 root 终端执行；普通桌面终端先执行 `sudo -i`。HDMI 工具会查找已登录的 X11 桌面会话。

```bash
cd /userdata/multi_video_ai_terminal/runtime
test -f config.json || cp config.example.json config.json
python3 tools/control.py start
python3 tools/control.py status
python3 tools/hdmi.py --fullscreen
```

电脑浏览器访问 `http://开发板IP:8090/`。在“通道配置”中填写 RTSP 地址或开发板本地文件的绝对路径，选中启用并保存。示例配置的四个通道默认关闭，适合首次部署后逐路配置。

```bash
python3 tools/hdmi.py --windowed
python3 tools/hdmi.py --restart --fullscreen
python3 tools/hdmi_health.py
python3 tools/hdmi.py --stop
python3 tools/control.py stop
```

`control.py` 管理本项目的进程并核对 PID、可执行文件和启动时间。推理调用挂起超过 15 秒时，程序以状态码 75 退出，监督进程最多重启三次。运行日志在 `data/terminal.log`，HDMI 日志在 `data/hdmi-preview.log`。

HDMI 需要已登录的 GNOME X11 桌面。播放器使用软件 MJPEG 解码，视频输入端使用 MPP 硬解。播放器显式关闭 HTTP seek，兼容当前板载 ffplay。后端看门狗重启后会替换播放器，防止旧连接留下停止刷新的窗口。`--restart` 可手动恢复预览，`hdmi_health.py` 检查本项目窗口的像素变化和播放器 CPU 计数。

若发生整机关机，可先检查 `journalctl -b -1 -u systemd-logind`。需要追踪异常电源键时，以具备输入设备读取权限的用户执行 `python3 tools/power_monitor.py --seconds 7200 --output data/power-events.jsonl`。该工具仅记录支持 KEY_POWER 的设备事件，不独占输入设备或修改电源策略。


当前板端按用户选择保留八路终端，旧 `uav-object-detection.service` 已停止，自启动设置未改。实测八路 640×640 约每路 16 FPS，详细条件见 `VALIDATION.md`。两套 NPU 检测并行时旧任务曾报 RKNN 错误，当前部署按择一运行管理。重启后若要继续使用八路终端，先执行 `systemctl stop uav-object-detection.service`，再运行本项目的启停命令。

## 八路预览与指标

配置第 5–8 路后，预览采用 3×3 布局：前八格显示视频，第九格显示设备摘要。每个视频格仍为 640×360，合成为 1920×1080；旧四路配置保持 1280×720。

第九格的 `Completed AI` 是已完成检测与后处理的总 FPS；`Preview (actual/target)` 是实际合成 FPS / 配置目标。`Board CPU` 是开发板整体 CPU 利用率，`RAM` 百分比是板端系统内存占比，`App RAM` 是当前终端进程 RSS（MB），`Temp` 来自板端温度传感器。这些指标均来自开发板。

`/api/status` 增加 `version`、`max_channels`、`layout` 和 `workers`。`workers` 显示各 NPU 核心当前阶段与忙碌时长，便于区分初始化、RGA、推理及后处理超时。看门狗仍只重启应用，不自动重启整机。

八路输入数量与预览客户端数量是不同概念；同时最多支持八个 MJPEG 订阅（含 HDMI）。

## 配置与接口

复制 `config.example.json` 为运行目录中的 `config.json` 后修改。相对路径以该配置所在目录为基准，通道 ID 为 0 到 7。旧四路配置启动时自动扩展为八个槽位，新增四路保持关闭且地址为空；保存配置时写入完整八路配置。模型必须满足单输入、640×640、COCO80 和九路 INT8 DFL 输出契约，类别表以 `person` 开头并包含 80 项。

默认置信度 0.35、NMS 阈值 0.45。每路 `alarm_classes` 使用 COCO 英文类别名，例如 `person`、`car`、`bottle`。目标持续出现 1 秒后触发，持续消失 2 秒后解除，同一路截图间隔至少 10 秒。该规则判断所选类别是否存在于画面中。

| 方法与地址 | 用途 |
|---|---|
| `GET /api/status` | 各路状态、实际帧率、计数、阶段耗时和设备资源 |
| `GET /api/config` | 当前完整配置 |
| `PUT /api/config` | 提交完整配置，保存后应用到对应通道 |
| `POST /api/channels/{id}/start` | 启用并持久保存单路配置 |
| `POST /api/channels/{id}/stop` | 停止并持久保存单路配置 |
| `GET /api/events?limit=30&before=0&channel=-1` | 按 ID 倒序查询事件，支持分页和通道筛选 |
| `GET /stream/mosaic.mjpg` | 四路 1280×720 或八路 1920×1080 MJPEG |
| `GET /snapshots/{filename}` | 查询事件对应截图 |

模型、类别表、监听地址、端口和存储参数需要停止程序后修改配置再启动。网页支持通道配置、阈值调整、启停和历史查询。事件时间使用开发板系统时间，持续时间和性能计数使用单调时钟。`latency_ms` 表示硬解出帧到检测完成的处理耗时。

SQLite 使用 WAL。截图先写临时文件再改名，数据库插入失败时清理对应图片，启动时清理未完成写入的孤立文件。后台每 30 秒检查保留期限和截图总量，默认 7 天、2048 MB，先清理最早事件。写盘故障及队列溢出显示在网页存储状态中。

同时最多支持八个预览订阅，包含 HDMI。达到上限时，新订阅返回 HTTP 503，网页稍后自动重试；状态和配置接口保留独立的服务线程。

## 构建与部署

源码采用 C++17、CMake、FFmpeg Rockchip、MPP、RGA、RKNN C API、OpenCV、TurboJPEG 和 SQLite。HTTP 使用 cpp-httplib，JSON 使用 nlohmann/json。板端构建复用现有私有 SDK 的头文件和库，安装时把所需 Rockchip、OpenCV 和 TurboJPEG 动态库复制到本项目运行目录。

首次准备依赖时，在电脑项目目录执行。

```powershell
python tools/prepare.py
python tools/prepare_model_inputs.py
```

此步骤下载固定版本 HTTP/JSON/SQLite 源码，复用相邻 `edgevision -yolov8` 中的后处理，并记录文件 SHA-256。已准备的模型在 `models/yolov8n`。模型转换使用现有 `uav-yolo11-converter:2.3.2` 镜像。

```powershell
docker run --rm --network none --entrypoint python3 -v "${PWD}:/project" -w /project uav-yolo11-converter:2.3.2 tools/convert_model.py --work /project/.deps/model-work --kind coco80
```

转换输入与校准数据位于 `.deps/model-work`，输出在其 `bundle/coco80` 中。将模型、类别表和清单复制到 `models/yolov8n`。转换过程检查九输出形状、COCO 类别顺序和 PyTorch/ONNX 输出误差，并分别保留校准、验证图像。

部署时显式指定 ADB 路径和设备序列号。

```powershell
python tools/deploy.py --serial 07c7cd5e6c097097 --adb "C:\Users\dvsense_docker\Desktop\work\RK3588\Tools\platform-tools\adb.exe"
```

部署脚本校验 RK3588 设备型号与源码包 SHA-256，然后在独立目录原生编译并运行 CTest。重建前先停止本项目。运行包附带 `build-info.json`、依赖清单、模型清单与 `SHA256SUMS`。Ubuntu 的 libc、libstdc++、libdrm、libz 和 libtbb 由当前系统提供。

## 性能与回归

八路分别持有独立采集解码线程和 FFmpeg 上下文。每路只保留一个待处理最新帧，调度器公平轮询，三个工作线程各自持有一个固定 NPU 核心的上下文。RGA 将线性 NV12 DMA 帧转换到 RKNN 输入缓冲区，预览使用可同步的 DMA32 缓冲区。结果保留原始帧引用、帧编号与连接代次。

采样包含浏览器等效 MJPEG 订阅，HDMI 应同时保持打开。

```bash
python3 tools/sample.py --channels 8 --warmup 30 --seconds 180 --preview-client --output data/eight-h264-180s
# 需要检查指定目标帧率时，显式添加 --require-fps 25。
```

输出 CSV 与 JSON，其中包含所选已启用通道 ID、各路平均有效检测 FPS、进程身份、预览接收率和原始状态样本。不设置 `--require-fps` 时，`passed` 仅表示采样完成、各路持续检测、进程与预览健康，不表示达到每路 25 FPS。进程重启或任一路平均检测低于目标时，命令返回失败。

电脑侧可用已有 COCO128 场景生成并发布四个独立测试源。

```powershell
python tools/fixtures.py generate --codec h264 --channels 8
python tools/fixtures.py serve --codec h264 --channels 8
# 复用已有四个验证片生成八个独立 RTSP 会话：
python tools/fixtures.py serve --codec h264 --channels 8 --reuse-fixtures
```

开发板输入填写 `rtsp://电脑局域网IP:8556/cam1` 到 `cam8`。H.265 测试使用 `--codec h265`，停止测试源使用另一个终端执行 `python tools/fixtures.py stop`。每个生成视频经归一化处理后包含 960 帧，时长 32 秒，规格为 1080p30。

```bash
cd /userdata/multi_video_ai_terminal/build
ctest --output-on-failure
python3 ../tests/integration.py
python3 ../tests/eight_channels.py  # 需要八路有效输入
```

在线回归会临时修改最后一个已启用通道，验证配置拒绝、单路停止、故障事件、其它通道持续检测和恢复，结束后恢复原配置。测试时段应与正式性能采样分开。

`python3 tests/lifecycle_clients.py` 在四路或八路正常运行时验证三个停止读取的预览客户端、二十次中途退出的客户端及八次单路启停。测试恢复原配置，检查其它通道计数持续推进，并记录测试前后的进程内存、线程和文件描述符数量。

模型基准支持一个或三个独立核心，重复分析同一硬解帧，输出单独的模型吞吐数据。

```bash
./bin/model_benchmark config.json /path/to/h264-0.mp4 1 30
./bin/model_benchmark config.json /path/to/h264-0.mp4 3 30
```

四路或八路纯硬解基准使用 `tools/benchmark_decode.py`，需要四个或八个本地视频路径。`tests/fault_integration.py` 通过显式加载测试库注入截图 ENOSPC 和一次 RKNN 返回错误，验证检测持续运行、存储恢复和推理上下文恢复。

## 交付包

在电脑项目目录执行 `python tools/package.py source`，在开发板源码目录执行 `python3 tools/package.py runtime`，生成独立源码 ZIP 和 ARM64 运行包。运行包解压后进入 `multi_video_ai_terminal`，复制 `config.example.json` 为 `config.json` 并填写视频源，再按上面的运行命令启动。运行包使用现有板载 FFmpeg 播放器和 X11 桌面显示 HDMI。

正式采样完成后执行 `python tools/package.py validation`，将 `data/validation` 中的 CSV、JSON、截图与诊断记录打包。输出位于 `releases`，`releases/SHA256SUMS` 记录所有交付压缩包的 SHA-256。运行包内另有各模型、程序、动态库和网页文件的校验清单。
