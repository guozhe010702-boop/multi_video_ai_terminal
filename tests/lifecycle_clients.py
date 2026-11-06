"""Exercise slow/disconnected preview clients and repeated channel start/stop."""
import argparse
import json
from pathlib import Path
import socket
import time
import urllib.parse
import urllib.request
import urllib.error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8090')
    parser.add_argument('--output', type=Path, default=Path('data/lifecycle-clients.json'))
    args = parser.parse_args()
    base = args.url.rstrip('/')
    endpoint = urllib.parse.urlsplit(base)
    if endpoint.scheme != 'http':
        parser.error('This board test requires HTTP')

    def request(path, method='GET', body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(base + path, data=data, method=method,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.load(response)

    def wait_channel(channel, running, after_count=-1):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            status = request('/api/status')
            item = status['channels'][channel]
            if (running and item['state'] == 'running' and item['detect_fps'] > 0 and item['detected'] > after_count) or (
                    not running and item['state'] == 'stopped'):
                return status
            time.sleep(.25)
        raise AssertionError('Channel failed to reach requested state')

    original = request('/api/config')
    initial = request('/api/status')
    active = [c['id'] for c in original['channels'] if c['enabled']]
    if len(active) not in (4,8) or not all(initial['channels'][i]['state']=='running' for i in active):
        raise RuntimeError('Enable four or eight valid running inputs before this test')
    target = max(active)
    others = [i for i in active if i != target]
    clients = []
    samples = []
    cycles = []
    admission_retries = 0
    try:
        # A small receive window and no reads force the server's writer to stall.
        for _ in range(3):
            client = socket.create_connection((endpoint.hostname, endpoint.port or 80), timeout=5)
            clients.append(client)
            client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
            client.sendall(('GET /stream/mosaic.mjpg HTTP/1.1\r\nHost: ' + endpoint.netloc + '\r\n\r\n').encode())
        for _ in range(15):
            time.sleep(1)
            samples.append(request('/api/status'))
        assert all(samples[-1]['channels'][i]['detected'] > initial['channels'][i]['detected'] + 15 for i in active)
        assert all(row['process_id'] == initial['process_id'] for row in samples)
        for client in clients:
            client.close()
        clients.clear()
        # Disconnect viewers during a JPEG transfer, then verify the API remains responsive.
        for _ in range(20):
            deadline = time.monotonic() + 10
            while True:
                try:
                    with urllib.request.urlopen(base + '/stream/mosaic.mjpg', timeout=5) as response:
                        assert response.read(1024)
                    break
                except urllib.error.HTTPError as error:
                    if error.code != 503 or time.monotonic() >= deadline:
                        raise
                    admission_retries += 1
                    assert request('/api/status')['process_id'] == initial['process_id']
                    time.sleep(.25)
        for index in range(8):
            before = request('/api/status')
            request(f'/api/channels/{target}/stop', 'POST')
            stopped = wait_channel(target, False)
            time.sleep(.5)
            request(f'/api/channels/{target}/start', 'POST')
            started = wait_channel(target, True, stopped['channels'][target]['detected'])
            time.sleep(.5)
            after = request('/api/status')
            assert all(after['channels'][i]['detected'] > before['channels'][i]['detected'] for i in others)
            assert after['process_id'] == initial['process_id']
            cycles.append({'cycle': index + 1, 'stopped': stopped, 'started': started, 'settled': after})
    finally:
        for client in clients:
            client.close()
        request('/api/config', 'PUT', original)
    time.sleep(6)
    final = wait_channel(target, True)
    before = initial['resources']
    after = final['resources']
    assert final['process_id'] == initial['process_id']
    assert all(final['channels'][i]['state'] == 'running' for i in active)
    assert after['fd_count'] <= before['fd_count'] + 8, 'File descriptors did not settle'
    assert after['threads'] <= before['threads'] + 3, 'Threads did not settle'
    assert after['rss_mb'] <= before['rss_mb'] + 32, 'Memory grew beyond viewer buffering allowance'
    report = {'passed': True, 'slow_clients': 3, 'slow_seconds': 15, 'aborted_viewers': 20,
              'preview_admission_retries': admission_retries,
              'channel_ids': active, 'target_channel': target, 'start_stop_cycles': len(cycles), 'initial': initial, 'final': final,
              'slow_samples': samples, 'cycles': cycles}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items()
                      if key not in ('initial', 'final', 'slow_samples', 'cycles')}, indent=2))


if __name__ == '__main__':
    main()
