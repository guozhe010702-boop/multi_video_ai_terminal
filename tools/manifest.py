import hashlib
import json
from pathlib import Path
import platform
import re
import subprocess
import sys

root = Path(sys.argv[1]).resolve()
info = {'system': platform.platform(), 'libc': platform.libc_ver(),
        'compiler': subprocess.check_output(['g++', '--version'], text=True).splitlines()[0],
        'dependencies': json.loads((root.parent / 'dependencies.lock.json').read_text()),
        'sdk': subprocess.check_output(['pkg-config', '--modversion', 'libavformat', 'libavcodec', 'libavutil', 'rockchip_mpp', 'librga'],
                                      env={**__import__('os').environ, 'PKG_CONFIG_PATH': '/userdata/av_dev/ffmpeg_pipeline/runtime/lib/pkgconfig'}, text=True).splitlines()}
driver = Path('/sys/kernel/debug/rknpu/version')
info['rknpu_driver'] = driver.read_text().strip() if driver.exists() else None
runtime_library = root / 'lib/librknnrt.so'
runtime_bytes = runtime_library.read_bytes()
strings = re.findall(rb'[\x20-\x7e]{8,}', runtime_bytes)
info['rknn_runtime'] = {
    'embedded_build_strings': [value.decode() for value in strings
        if re.fullmatch(rb'\d+\.\d+\.\d+ \([^\n]{5,100}@[^\n]{5,100}\)', value)],
    'sha256': hashlib.sha256(runtime_bytes).hexdigest(),
    'version_source': 'embedded runtime library build string'}
model_path = Path('/proc/device-tree/model')
info['device_model'] = model_path.read_text().rstrip('\0\n') if model_path.exists() else None
info['os_release'] = Path('/etc/os-release').read_text()
info['source_sha256'] = {}
for folder in ('src', 'include', 'vendor/yolo'):
    for path in sorted((root.parent / folder).rglob('*')):
        if path.is_file():
            info['source_sha256'][path.relative_to(root.parent).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
(root / 'build-info.json').write_text(json.dumps(info, indent=2))
lines = []
for path in sorted(root.rglob('*')):
    relative = path.relative_to(root)
    if path.is_file() and '__pycache__' not in relative.parts and (relative.parts[0] in ('bin', 'lib', 'models', 'web', 'tools', 'licenses') or
                          relative.as_posix() in ('build-info.json', 'config.example.json', 'README.md', 'VALIDATION.md')):
        lines.append(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.relative_to(root).as_posix())
(root / 'SHA256SUMS').write_text('\n'.join(lines) + '\n')
