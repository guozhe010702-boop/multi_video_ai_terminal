# 通用场景连续视频素材包 general-motion-v1

9 段来自 Wikimedia Commons 的真实视频组成 8 路不同场景，独立播放内容合计 **706.77 秒（约 11 分 47 秒）**，转码文件合计 **215.5 MiB**。鸟类一路顺序拼接两段不同视频，其余通道各用一段原片；各通道使用不同素材，播放到结尾后循环。

| 通道 ID | 界面名称 | 内容 | 时长 | 编码 |
|---|---|---|---|---|
| 0 | Street | 街道人群 | 40.27s | H.264 |
| 1 | Traffic | 夜间交通 | 159.87s | H.265 |
| 2 | Indoor | 办公室清洁 | 160.97s | H.264 |
| 3 | Kitchen | 厨房烹饪 | 60.10s | H.265 |
| 4 | Cats | 猫 | 79.30s | H.264 |
| 5 | Dogs | 海滩上的狗 | 76.97s | H.265 |
| 6 | Birds | 鸭群 | 69.27s | H.264 |
| 7 | Sports | 篮球 | 60.03s | H.265 |

输出统一为 1920×1080、30 FPS、YUV420P，保持原始宽高比并补黑边，去除音轨。室内原片为 854×478，猫原片为 1280×720，其中一段鸭群原片为 3840×2160；其余原片为 1920×1080。输出规格经过归一化，不代表所有原片都是原生 1080p30。部分素材为多镜头拍摄的视频，保留原有切镜。

这是演示与链路检查素材，没有人工框标注，不能据此给出 mAP、召回率或漏检率结论。检测继续使用 YOLOv8n INT8、640×640、置信度 0.35、NMS 0.45；告警类别保持原配置。

## 文件与来源

- `catalog.json`：9 段原片的作者、许可、来源链接、原始规格与 SHA-256；8 路编排与修改版许可。
- `manifest.json`：8 个转码文件的时长、规格、编码与 SHA-256。
- [CREDITS.md](CREDITS.md)：逐片来源与许可。转码、去音轨、补边和拼接均已声明；各片按对应许可提供，鸟类拼接版使用 CC BY-SA 4.0。
- `SHA256SUMS`：解压后核对素材和元数据；视频位于 `videos/`。
- `VALIDATION.json`：本次短测摘要；项目 `data/validation/demo-media-v1-*` 保留完整 JSON/CSV 和截图。

## 当前开发板

素材目录：`/userdata/multi_video_ai_terminal/demo_media/general-motion-v1/`。
运行目录：`/userdata/multi_video_ai_terminal/runtime/`。
网页：`http://192.168.2.221:8090/`，HDMI 订阅同一份九宫格预览。

程序通过 `/api/config` 切换八路名称和视频源，保留模型、阈值、告警与存储配置。切换前完整配置保存到运行目录 `data/config-backups/`，备份路径由命令输出。旧检测服务仍停止，自启动设置保持原样。

```bash
cd /userdata/multi_video_ai_terminal/runtime
python3 tools/use_demo_media.py --media-dir /userdata/multi_video_ai_terminal/demo_media/general-motion-v1
# 用上述命令实际输出的备份路径恢复：
python3 tools/use_demo_media.py --restore data/config-backups/<备份文件>.json
```

## 重新制作与部署

电脑安装 FFmpeg / ffprobe 和 Python 3.8+，从本项目根目录执行：

```powershell
python tools/prepare_demo_media.py --download
```

工具先核对原片 SHA-256，再逐路转码，默认两路并行。来源站返回 429 时会报告应等待的秒数，等待后再运行即可；已下载并通过校验的原片不会重复下载。使用打包的工具时显式指定路径：

```powershell
python general-motion-v1/tools/prepare_demo_media.py --catalog general-motion-v1/catalog.json --output general-motion-v1 --download
```

素材包解压后可直接上传 `general-motion-v1` 整个目录至板端 `/userdata/multi_video_ai_terminal/demo_media/`，将包内 `tools/use_demo_media.py` 复制到终端运行目录的 `tools/`，再执行切换命令。工具在切换前核对全部八个文件的 SHA-256，并保存旧配置。

重新编码时，不同 FFmpeg/编码器版本可能生成不同的输出 SHA-256；以本次生成的 `manifest.json` 为准。原片 SHA-256 固定。该包独立于已封存的 v1.1.0 程序交付件，不覆盖历史压测素材或历史性能数据。
