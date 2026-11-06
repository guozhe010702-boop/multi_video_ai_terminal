"""Download pinned public demo clips and normalize eight distinct video channels.

Requires ffmpeg/ffprobe on PATH. Downloads are opt-in and sequential; originals
and generated media live under .deps, separate from the historical fixtures.
"""
import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PACK = 'general-motion-v1'


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def probe(path):
    return json.loads(subprocess.check_output([
        'ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)]))


def original(asset, folder, download):
    target = folder / 'originals' / asset['file']
    if target.is_file() and digest(target) == asset['sha256']:
        return target
    if not download:
        raise RuntimeError('Missing/mismatched original {}; use --download to fetch it'.format(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + '.part')
    request = urllib.request.Request(asset['download_url'], headers={
        'User-Agent': 'RK3588-demo-media/1.0 (public video evaluation)'})
    try:
        with urllib.request.urlopen(request, timeout=90) as response, temporary.open('wb') as output:
            shutil.copyfileobj(response, output, 1024 * 1024)
    except urllib.error.HTTPError as error:
        if error.code == 429:
            raise RuntimeError('Source rate limit: wait {} seconds before retrying'.format(
                error.headers.get('Retry-After', '600'))) from error
        raise
    if digest(temporary) != asset['sha256']:
        raise RuntimeError('Downloaded source SHA-256 differs: ' + asset['file'])
    temporary.replace(target)
    return target


def encode(channel, paths, folder, overwrite):
    outputs = folder / 'videos'
    outputs.mkdir(parents=True, exist_ok=True)
    target = outputs / channel['file']
    # Existing files must have a matching manifest, never silently reuse a stale encode.
    stamp = target.with_suffix('.json')
    signature = {'sources': channel['sources'], 'codec': channel['codec'],
                 'profile': '1080p30-letterbox-muted-crf25-v1'}
    if not overwrite and target.exists() and stamp.exists():
        saved = json.loads(stamp.read_text(encoding='utf-8'))
        if saved.get('signature') == signature and saved.get('sha256') == digest(target):
            return saved
    temporary = target.with_suffix('.part.mp4')
    command = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y']
    for asset_id in channel['sources']:
        command += ['-threads', '2', '-i', str(paths[asset_id])]
    filters = []
    for index in range(len(channel['sources'])):
        filters.append('[{}:v:0]scale=1920:1080:force_original_aspect_ratio=decrease:force_divisible_by=2,'
                       'pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p,'
                       'setpts=PTS-STARTPTS[v{}]'.format(index, index))
    inputs = ''.join('[v{}]'.format(i) for i in range(len(channel['sources'])))
    filters.append(inputs + 'concat=n={}:v=1:a=0[out]'.format(len(channel['sources'])))
    command += ['-filter_complex_threads', '1', '-filter_complex', ';'.join(filters), '-map', '[out]', '-an']
    if channel['codec'] == 'hevc':
        command += ['-c:v', 'libx265', '-preset', 'ultrafast', '-crf', '25', '-tag:v', 'hvc1',
                    '-x265-params', 'pools=2:frame-threads=1:log-level=error:keyint=60']
    else:
        command += ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '25', '-g', '60', '-threads', '2']
    command += ['-movflags', '+faststart', str(temporary)]
    subprocess.run(command, check=True)
    info = probe(temporary)
    video = next(x for x in info['streams'] if x['codec_type'] == 'video')
    if (video['width'], video['height'], video['pix_fmt'], video['codec_name']) != (
            1920, 1080, 'yuv420p', channel['codec']):
        raise RuntimeError('Unexpected output format for ' + channel['file'])
    if video['avg_frame_rate'] != '30/1':
        raise RuntimeError('Unexpected output FPS for ' + channel['file'])
    temporary.replace(target)
    result = {'channel': channel['id'], 'name': channel['name'], 'file': channel['file'],
              'signature': signature, 'sha256': digest(target), 'bytes': target.stat().st_size,
              'duration_seconds': float(info['format']['duration']), 'width': 1920, 'height': 1080,
              'fps': 30, 'codec': video['codec_name'], 'pixel_format': video['pix_fmt']}
    stamp.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print('{} ready: {:.1f}s, {:.1f}MB'.format(channel['name'], result['duration_seconds'],
                                            result['bytes'] / 1048576), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=ROOT / 'demo_media' / PACK / 'catalog.json')
    parser.add_argument('--output', type=Path, default=ROOT / '.deps' / 'demo-media' / PACK)
    parser.add_argument('--download', action='store_true', help='Fetch missing originals, checking pinned SHA-256')
    parser.add_argument('--overwrite', action='store_true')
    parser.add_argument('--jobs', type=int, choices=(1, 2), default=2)
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text(encoding='utf-8'))
    paths = {asset['id']: original(asset, args.output, args.download) for asset in catalog['assets']}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(lambda channel: encode(channel, paths, args.output, args.overwrite), catalog['channels']))
    (args.output / 'manifest.json').write_text(json.dumps({
        'pack': PACK, 'videos': results, 'ffmpeg_version': subprocess.check_output(
            ['ffmpeg', '-version'], text=True).splitlines()[0]}, indent=2) + '\n', encoding='utf-8')
    print('Total distinct playback: {:.1f}s'.format(sum(x['duration_seconds'] for x in results)))


if __name__ == '__main__':
    main()
