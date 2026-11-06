"""Generate reproducible COCO-scene clips and serve four or eight independent RTSP paths."""
import argparse
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def generate(codec, channels):
    folder = ROOT / '.deps/fixtures'
    folder.mkdir(parents=True, exist_ok=True)
    images = sorted((ROOT / '.deps/model-work/datasets/coco128/images/train2017').glob('*.jpg'))
    if len(images) < 32:
        raise RuntimeError('Prepare COCO128 images before generating clips')
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('FFmpeg is required on the computer')
    for channel in range(channels):
        target = folder / (codec + '-' + str(channel) + '.mp4')
        if target.exists():
            probe = json.loads(subprocess.check_output([shutil.which('ffprobe'), '-v', 'error', '-select_streams', 'v:0',
                '-show_entries', 'stream=avg_frame_rate,nb_frames', '-of', 'json', str(target)]))['streams'][0]
            if probe['avg_frame_rate'] == '30/1' and int(probe['nb_frames']) == 960: continue
        sequence = folder / ('images-' + str(channel))
        sequence.mkdir(exist_ok=True)
        selected = images[channel % 4::4][:32]
        for index in range(32):
            image = selected[(index + (channel // 4) * 8) % len(selected)]
            normalized = sequence / ('%03d.jpg' % index)
            # Normalize dimensions and pixel format before image2 decoding.
            # Variable-size JPEGs otherwise reinitialize the fps filter and lose frames.
            subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-i', str(image),
                '-vf', 'scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2',
                '-pix_fmt', 'yuvj420p', '-frames:v', '1', '-update', '1', str(normalized)], check=True)
        options = ['-c:v', 'libx264', '-preset', 'ultrafast', '-threads', '2'] if codec == 'h264' else [
            '-c:v', 'libx265', '-preset', 'ultrafast', '-x265-params', 'pools=2:frame-threads=2:log-level=error']
        subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-framerate', '1',
            '-i', str(sequence / '%03d.jpg'), '-vf', 'fps=30',
            *options, '-crf', '28', '-g', '30', '-bf', '0', '-pix_fmt', 'yuv420p', str(target)], check=True)
        print(target, flush=True)


def serve(codec, channels, reuse):
    folder = ROOT / '.deps/fixtures'
    binary = folder / ('mediamtx.exe' if sys.platform == 'win32' else 'mediamtx')
    if not binary.exists():
        if sys.platform != 'win32':
            raise RuntimeError('Pass an installed MediaMTX binary on this platform')
        url = 'https://github.com/bluenviron/mediamtx/releases/download/v1.21.2/mediamtx_v1.21.2_windows_amd64.zip'
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            binary.write_bytes(archive.read('mediamtx.exe'))
        (folder / 'mediamtx-source.json').write_text(json.dumps({'source':url,'sha256':__import__('hashlib').sha256(data).hexdigest()}))
    config = folder / 'mediamtx.yml'
    config.write_text('logLevel: warn\nrtspAddress: :8556\nrtspTransports: [tcp]\nrtmp: no\nhls: no\nwebrtc: no\nsrt: no\nmoq: no\npaths:\n  all_others:\n')
    stop_file = folder / 'server.stop'
    if stop_file.exists(): stop_file.unlink()
    clips = []
    for channel in range(channels):
        clip = folder / (codec + '-' + str(channel) + '.mp4')
        if not clip.exists() and reuse:
            clip = folder / (codec + '-' + str(channel % 4) + '.mp4')
        if not clip.exists(): raise FileNotFoundError(clip)
        clips.append(clip)
    (folder / 'source-map.json').write_text(json.dumps({'channels': channels, 'codec': codec,
        'sources': [{'path': 'cam'+str(i+1), 'clip':str(clip)} for i,clip in enumerate(clips)]},indent=2))
    processes = []
    try:
        with (folder / 'rtsp.log').open('ab') as log:
            processes.append(subprocess.Popen([str(binary), str(config)], cwd=folder, stdout=log, stderr=log))
            time.sleep(2)
            if processes[0].poll() is not None:
                raise RuntimeError('MediaMTX exited, inspect .deps/fixtures/rtsp.log')
            for channel in range(channels):
                clip = clips[channel]
                processes.append(subprocess.Popen([shutil.which('ffmpeg'), '-hide_banner', '-loglevel', 'warning',
                    '-re', '-stream_loop', '-1', '-i', str(clip), '-an', '-c', 'copy', '-f', 'rtsp',
                    '-rtsp_transport', 'tcp', 'rtsp://127.0.0.1:8556/cam' + str(channel + 1)], stdout=log, stderr=log))
            print(str(channels) + ' ' + codec + ' 1080p30 RTSP sources ready at 127.0.0.1:8556/cam1..cam' + str(channels), flush=True)
            while all(p.poll() is None for p in processes):
                if stop_file.exists(): return
                time.sleep(1)
            raise RuntimeError('A fixture process exited, inspect rtsp.log')
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('generate','serve','stop'))
    parser.add_argument('--codec', choices=('h264','h265'), default='h264')
    parser.add_argument('--channels', type=int, choices=(4,8), default=8)
    parser.add_argument('--reuse-fixtures', action='store_true', help='Reuse the four validated clips for additional independent RTSP sessions')
    args = parser.parse_args()
    if args.action == 'stop':
        (ROOT / '.deps/fixtures/server.stop').touch()
    elif args.action == 'generate': generate(args.codec, args.channels)
    else: serve(args.codec, args.channels, args.reuse_fixtures)
