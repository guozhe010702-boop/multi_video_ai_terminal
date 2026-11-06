"""Deploy this independent project to one explicitly selected RK3588."""
import argparse
import hashlib
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--serial', required=True)
    parser.add_argument('--adb', type=Path, required=True)
    args = parser.parse_args()

    def adb(*command):
        return subprocess.check_output([str(args.adb), '-s', args.serial, *command]).decode('utf-8', errors='replace').replace('\r', '')

    model = adb('shell', 'cat /proc/device-tree/model')
    if 'RK3588' not in model:
        raise RuntimeError('Selected device is not an RK3588')
    archive = ROOT / '.deps/source.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        for name in ('CMakeLists.txt', 'include', 'src', 'vendor', 'web', 'tools', 'tests', 'models',
                     'config.example.json', 'dependencies.lock.json', 'README.md', 'VALIDATION.md', '.deps/include', '.deps/src'):
            path = ROOT / name
            if not path.exists():
                raise FileNotFoundError(path)
            tar.add(path, arcname=name, filter=lambda entry: None if '__pycache__' in entry.name else entry)
    remote = '/userdata/multi_video_ai_terminal'
    print(adb('shell', 'mkdir -p ' + remote))
    print(adb('push', str(archive), remote + '/source.tar.gz'))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    code = """
import hashlib,subprocess,tarfile
from pathlib import Path
root=Path('/userdata/multi_video_ai_terminal')
archive=root/'source.tar.gz'
if hashlib.sha256(archive.read_bytes()).hexdigest()!=DIGEST: raise SystemExit('Archive checksum mismatch')
for process in Path('/proc').iterdir():
    if not process.name.isdigit(): continue
    try: target=(process/'exe').resolve()
    except OSError: continue
    if target==root/'runtime/bin/multi_video_ai_terminal': raise SystemExit('Stop the terminal before deployment')
with tarfile.open(archive) as tar:
    for member in tar.getmembers():
        path=(root/member.name).resolve()
        if root not in path.parents or member.issym() or member.islnk(): raise SystemExit('Unsafe archive entry')
    tar.extractall(root)
result=subprocess.run(['bash',str(root/'tools/build_board.sh')],cwd=root)
print('__MULTI_BUILD_OK__' if result.returncode==0 else '__MULTI_BUILD_FAILED__')
""".replace('DIGEST', repr(digest))
    command = "python3 -c 'exec(bytes([{}]))'".format(','.join(str(x) for x in code.encode()))
    output = adb('shell', command)
    print(output)
    if '__MULTI_BUILD_OK__' not in output:
        raise SystemExit('Board build failed')


if __name__ == '__main__':
    main()
