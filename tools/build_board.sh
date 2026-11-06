#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
test "$(uname -m)" = aarch64
test -f vendor/httplib.h
test -f .deps/src/sqlite3.c
python3 - "$ROOT/runtime/bin/multi_video_ai_terminal" <<'PY'
import os,sys
from pathlib import Path
target=Path(sys.argv[1]).resolve()
for p in Path('/proc').iterdir():
    if not p.name.isdigit(): continue
    try: executable=Path(os.readlink(str(p/'exe')).split(' (deleted)',1)[0])
    except OSError: continue
    if executable==target: raise SystemExit('Stop this terminal before rebuilding it')
PY
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=ON -DCMAKE_INSTALL_PREFIX="$ROOT/runtime"
cmake --build build --parallel 4
(cd build && ctest --output-on-failure)
cmake --install build
mkdir -p runtime/lib runtime/models runtime/licenses
cp /userdata/edgevision-yolov8/upstream/rknn/librknnrt.so runtime/lib/
cp -a /userdata/av_dev/ffmpeg_pipeline/runtime/lib/librga.so* runtime/lib/
cp -a /userdata/av_dev/ffmpeg_pipeline/runtime/lib/librockchip_mpp.so* runtime/lib/
for library in libopencv_core.so libopencv_imgproc.so libturbojpeg.so; do
    cp -a /userdata/edgevision-yolov8/build-deps/root/usr/lib/aarch64-linux-gnu/"$library"* runtime/lib/
done
cp -a models/. runtime/models/
cp -a vendor/licenses/. runtime/licenses/
cp -a /userdata/edgevision-yolov8/upstream/licenses/. runtime/licenses/rockchip/
mkdir -p runtime/licenses/MPP
cp -a /userdata/av_dev/ffmpeg_pipeline/build/mpp/LICENSES/. runtime/licenses/MPP/
cp /userdata/av_dev/ffmpeg_pipeline/build/rga/COPYING runtime/licenses/RGA-COPYING
for license in COPYING.LGPLv3 COPYING.LGPLv2.1 COPYING.GPLv3 COPYING.GPLv2; do
    cp /userdata/av_dev/ffmpeg_pipeline/build/ffmpeg/"$license" runtime/licenses/"FFmpeg-$license"
done
for package in libopencv-core4.2 libopencv-imgproc4.2 libturbojpeg; do
    cp /userdata/edgevision-yolov8/build-deps/root/usr/share/doc/"$package"/copyright runtime/licenses/"$package-copyright"
done
test -e runtime/config.json || cp config.example.json runtime/config.json
python3 tools/manifest.py runtime
ldd runtime/bin/multi_video_ai_terminal
