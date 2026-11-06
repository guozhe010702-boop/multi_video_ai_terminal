"""Measure completed detection counters for enabled inputs, including eight-channel runs."""
import argparse
import csv
import json
from pathlib import Path
import time
import threading
import urllib.request


def read(url):
    with urllib.request.urlopen(url.rstrip('/') + '/api/status', timeout=5) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8090')
    parser.add_argument('--seconds', type=int, default=1800)
    parser.add_argument('--warmup', type=int, default=60)
    parser.add_argument('--output', type=Path, default=Path('data/acceptance'))
    parser.add_argument('--require-fps', type=float)
    parser.add_argument('--channels', type=int, choices=(4,8), help='Require exactly this many enabled inputs')
    parser.add_argument('--preview-client', action='store_true', help='Consume the same MJPEG feed as a browser')
    args = parser.parse_args()
    if args.seconds < 1 or args.warmup < 0:
        parser.error('Invalid sampling duration')
    finished = threading.Event()
    preview = {'frames': 0, 'errors': []}
    def consume():
        try:
            with urllib.request.urlopen(args.url.rstrip('/') + '/stream/mosaic.mjpg', timeout=10) as response:
                while not finished.is_set():
                    line = response.readline()
                    if not line:
                        if not finished.is_set(): raise EOFError('Preview stream ended')
                        break
                    if line.lower().startswith(b'content-length:'):
                        size = int(line.split(b':',1)[1])
                        response.readline()
                        remaining = size
                        while remaining:
                            chunk = response.read(remaining)
                            if not chunk: raise EOFError('Preview stream ended')
                            remaining -= len(chunk)
                        preview['frames'] += 1
        except Exception as error:
            preview['errors'].append(str(error))
    client = threading.Thread(target=consume, daemon=True) if args.preview_client else None
    if client: client.start()
    time.sleep(args.warmup)
    preview_start = preview['frames']
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        first = read(args.url)
    except Exception as error:
        args.output.with_suffix('.json').write_text(json.dumps({'passed':False,'error':str(error),'samples':[]},indent=2))
        finished.set()
        raise
    started = time.monotonic()
    channel_ids = [c['id'] for c in first['channels'] if c.get('enabled', c['state'] != 'stopped')]
    if not channel_ids or (args.channels is not None and len(channel_ids) != args.channels):
        finished.set()
        raise RuntimeError('Enabled input count does not match the requested sampling profile')
    initial_channels = {c['id']: c for c in first['channels']}
    samples = []
    sampling_errors = []
    with args.output.with_suffix('.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['elapsed_s', 'channel', 'state', 'detected', 'detect_fps', 'decode_fps',
            'latency_ms', 'dropped', 'cpu_pct', 'rss_mb', 'temperature_c', 'mosaic_fps'])
        writer.writeheader()
        while True:
            elapsed = time.monotonic() - started
            try:
                status = read(args.url)
            except Exception as error:
                sampling_errors.append({'elapsed_s':elapsed,'error':str(error)})
                if elapsed >= args.seconds or len(sampling_errors) >= 15: break
                time.sleep(1)
                continue
            samples.append(status)
            for c in status['channels']:
                row = {key: c.get(key) for key in ('state', 'detected', 'detect_fps', 'decode_fps', 'latency_ms', 'dropped')}
                row.update(elapsed_s=elapsed, channel=c['id'], mosaic_fps=status['mosaic_fps'])
                row.update({key: status['resources'].get(key) for key in ('cpu_pct', 'rss_mb', 'temperature_c')})
                writer.writerow(row)
            stream.flush()
            if elapsed >= args.seconds:
                break
            time.sleep(min(1, args.seconds - elapsed))
    last = samples[-1] if samples else first
    final_channels = {c['id']: c for c in last['channels']}
    fps = [(final_channels[i]['detected'] - initial_channels[i]['detected']) / elapsed for i in channel_ids]
    identities = sorted({first.get('process_id'), *(sample.get('process_id') for sample in samples)})
    unhealthy_samples = sum(c['state'] != 'running' for sample in samples for c in sample['channels'] if c['id'] in channel_ids)
    finished.set()
    if client: client.join(timeout=11)
    received_fps = (preview['frames'] - preview_start) / elapsed if client else None
    complete = elapsed >= args.seconds
    target_met = None if args.require_fps is None else all(x >= args.require_fps for x in fps)
    report = {'seconds': elapsed, 'requested_seconds': args.seconds, 'completed': complete,
              'channel_ids': channel_ids, 'average_detect_fps': fps, 'total_detect_fps': sum(fps),
              'required_fps': args.require_fps, 'process_ids': identities,
              'performance_target_met': target_met, 'unhealthy_channel_samples': unhealthy_samples,
              'preview_received_fps': received_fps,
              'preview_errors': preview['errors'],
              'sampling_errors': sampling_errors,
              'passed': complete and all(x > 0 for x in fps) and target_met is not False and len(identities) == 1 and not unhealthy_samples
                        and not preview['errors'] and not sampling_errors and (received_fps is None or received_fps >= 14.5),
              'initial': first, 'final': last, 'samples': samples}
    args.output.with_suffix('.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key not in ('initial', 'final', 'samples')}, indent=2))
    if report['passed'] is False:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
