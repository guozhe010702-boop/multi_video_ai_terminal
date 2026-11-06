"""Prepare pinned headers, SQLite and audited local YOLO postprocessing."""
import hashlib
import io
import json
from pathlib import Path
import shutil
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    'vendor/httplib.h': 'https://raw.githubusercontent.com/yhirose/cpp-httplib/v0.18.3/httplib.h',
    'vendor/nlohmann/json.hpp': 'https://raw.githubusercontent.com/nlohmann/json/v3.11.3/single_include/nlohmann/json.hpp',
    'vendor/licenses/httplib.txt': 'https://raw.githubusercontent.com/yhirose/cpp-httplib/v0.18.3/LICENSE',
    'vendor/licenses/json.txt': 'https://raw.githubusercontent.com/nlohmann/json/v3.11.3/LICENSE.MIT',
}


def download(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def main():
    manifest = {}
    for name, url in SOURCES.items():
        path = ROOT / name
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(download(url))
        manifest[name] = {'source': url, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    sqlite_url = 'https://www.sqlite.org/2025/sqlite-amalgamation-3490100.zip'
    archive_path = ROOT / '.deps/sqlite-amalgamation.zip'
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    if not archive_path.exists():
        archive_path.write_bytes(download(sqlite_url))
    with zipfile.ZipFile(io.BytesIO(archive_path.read_bytes())) as archive:
        for name in ('sqlite3.h', 'sqlite3.c'):
            member = next(item for item in archive.namelist() if item.endswith('/' + name))
            target = ROOT / '.deps' / ('include' if name.endswith('.h') else 'src') / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(member))
    manifest['sqlite'] = {'source': sqlite_url, 'sha256': hashlib.sha256(archive_path.read_bytes()).hexdigest()}
    upstream = ROOT.parent / 'edgevision -yolov8/部署/RK3588/c_tuili'
    for name in ('postprocess.cc', 'postprocess.h', 'yolov8.h'):
        source = upstream / 'examples/yolov8/cpp' / name
        target = ROOT / 'vendor/yolo' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        manifest['vendor/yolo/' + name] = {'source': str(source), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
    for name in ('common.h', 'image_utils.h'):
        shutil.copy2(upstream / 'utils' / name, ROOT / 'vendor/yolo' / name)
    model_license = ROOT.parent / 'edgevision -yolov8/训练/ultralytics_yolov8-main/LICENSE'
    if model_license.exists():
        shutil.copy2(model_license, ROOT / 'vendor/licenses/ultralytics.txt')
    sdk_versions = ROOT.parent / 'board/ffmpeg_pipeline/versions.json'
    if sdk_versions.exists():
        manifest['multimedia_sdk'] = json.loads(sdk_versions.read_text(encoding='utf-8'))
    # Local provenance stays beside every copied upstream implementation.
    (ROOT / 'dependencies.lock.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')
    print('Private dependencies and model postprocessing prepared')


if __name__ == '__main__':
    main()
