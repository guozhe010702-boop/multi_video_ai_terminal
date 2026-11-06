"""Measure four or eight concurrent MPP decoders without inference or preview."""
import argparse
import json
from pathlib import Path
import subprocess
import time

parser = argparse.ArgumentParser()
parser.add_argument('--ffmpeg', default='/userdata/av_dev/ffmpeg_pipeline/runtime/bin/ffmpeg')
parser.add_argument('--codec', choices=('h264','h265'), default='h264')
parser.add_argument('--output', type=Path, default=Path('data/decode-baseline.json'))
parser.add_argument('videos', nargs='+')
args = parser.parse_args()
if len(args.videos) not in (4,8): parser.error('Provide four or eight local video files')
processes = []
started = time.monotonic()
try:
    for video in args.videos:
        processes.append(subprocess.Popen([args.ffmpeg,'-hide_banner','-loglevel','error','-nostats',
            '-hwaccel','rkmpp','-hwaccel_output_format','drm_prime','-c:v','h264_rkmpp' if args.codec=='h264' else 'hevc_rkmpp',
            '-i',video,'-an','-frames:v','900','-progress','pipe:1','-f','null','-'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True))
    rows = []
    for process in processes:
        output,error = process.communicate(timeout=60)
        frames = [int(line.split('=',1)[1]) for line in output.splitlines() if line.startswith('frame=')]
        rows.append({'returncode':process.returncode,'frames':frames[-1] if frames else 0,'error':error})
    elapsed = time.monotonic()-started
    result = {'kind':str(len(args.videos))+' concurrent MPP decoders, unpaced files','codec':args.codec,'seconds':elapsed,
              'channels':rows,'total_frames_per_second':sum(row['frames'] for row in rows)/elapsed,
              'passed':all(row['returncode']==0 and row['frames']==900 for row in rows)}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
    if not result['passed']: raise SystemExit(1)
finally:
    for process in processes:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
