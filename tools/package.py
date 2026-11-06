"""Create source/runtime deliveries without user configuration or event data."""
import argparse
import hashlib
import re
import shutil
from pathlib import Path
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('kind', choices=('source', 'runtime', 'validation'))
args = parser.parse_args()
output = ROOT / 'releases'
output.mkdir(exist_ok=True)
previous=output/'SHA256SUMS'
legacy=output/'SHA256SUMS-1.0.0'
if previous.exists() and not legacy.exists() and '1.1.0' not in previous.read_text(): shutil.copy2(previous,legacy)
if args.kind == 'source':
    archive = output / 'multi-video-ai-terminal-1.1.0-source.zip'
    paths = ['CMakeLists.txt', '.gitignore', 'config.example.json', 'dependencies.lock.json', 'README.md', 'VALIDATION.md',
             'include', 'src', 'web', 'tools', 'tests', 'vendor', '.deps/include', '.deps/src', 'models/yolov8n']
    for name in paths:
        if not (ROOT / name).exists():
            raise FileNotFoundError(ROOT / name)
    temporary = archive.with_name(archive.name + '.tmp')
    with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as target:
        for name in paths:
            path = ROOT / name
            files = sorted(path.rglob('*')) if path.is_dir() else [path]
            for file in files:
                if file.is_file() and '__pycache__' not in file.parts:
                    target.write(file, 'multi_video_ai_terminal/' + file.relative_to(ROOT).as_posix())
    temporary.replace(archive)
elif args.kind == 'runtime':
    archive = output / 'multi-video-ai-terminal-1.1.0-linux-aarch64.tar.gz'
    runtime = ROOT / 'runtime'
    paths = ['bin', 'lib', 'models', 'web', 'tools', 'licenses', 'build-info.json', 'SHA256SUMS', 'config.example.json', 'README.md', 'VALIDATION.md']
    temporary = archive.with_name(archive.name + '.tmp')
    with tarfile.open(temporary, 'w:gz', compresslevel=6) as target:
        for name in paths:
            path = runtime / name
            if not path.exists():
                raise FileNotFoundError(path)
            target.add(path, arcname='multi_video_ai_terminal/' + name,
                       filter=lambda entry: None if '__pycache__' in entry.name else entry)
    temporary.replace(archive)
else:
    archive = output / 'multi-video-ai-terminal-1.1.0-validation.zip'
    reports = ROOT / 'data/validation'
    for name in ('eight-h264-180s.csv', 'eight-h264-180s.json', 'eight-h265-180s.csv', 'eight-h265-180s.json', 'eight-functional.json'):
        if not (reports / name).is_file():
            raise FileNotFoundError(reports / name)
    temporary = archive.with_name(archive.name + '.tmp')
    with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as target:
        for file in sorted(reports.iterdir()):
            if file.is_file() and (file.suffix in ('.csv', '.json', '.jsonl', '.jpg') or file.name.endswith('SHA256SUMS') or file.name == 'poweroff-origin.log'):
                target.write(file, 'validation/' + file.name)
        target.write(ROOT / 'VALIDATION.md', 'VALIDATION.md')
    temporary.replace(archive)
rows = []
for file in sorted(output.iterdir()):
    if file.is_file() and re.fullmatch(r'multi-video-ai-terminal-[0-9]+\.[0-9]+\.[0-9]+-(source\.zip|validation\.zip|linux-aarch64\.tar\.gz)',file.name):
        rows.append(hashlib.sha256(file.read_bytes()).hexdigest() + '  ' + file.name)
(output / 'SHA256SUMS').write_text('\n'.join(rows) + '\n')
(output/'SHA256SUMS-1.1.0').write_text('\n'.join(row for row in rows if 'multi-video-ai-terminal-1.1.0-' in row)+'\n')
print(archive)
