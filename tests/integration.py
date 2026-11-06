"""Live API regression. Temporarily modifies the last enabled channel, then restores config."""
import argparse
import copy
import json
import time
import urllib.error
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('--url', default='http://127.0.0.1:8090')
args = parser.parse_args()


def request(path, method='GET', data=None):
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(args.url + path, data=body, method=method, headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.load(response)


original = request('/api/config')
active = [c['id'] for c in original['channels'] if c['enabled']]
assert active, 'Enable valid inputs before testing'
target = max(active)
others = [i for i in active if i != target]
try:
    bad = copy.deepcopy(original)
    bad['confidence'] = 2
    try:
        request('/api/config','PUT',bad)
        raise AssertionError('Invalid confidence was accepted')
    except urllib.error.HTTPError as error:
        assert error.code == 400
    assert request('/api/config') == original
    baseline = request('/api/status')
    request(f'/api/channels/{target}/stop','POST')
    assert request('/api/status')['channels'][target]['state'] == 'stopped'
    broken = copy.deepcopy(original)
    broken['channels'][target]['source'] = 'rtsp://127.0.0.1:9/unavailable'
    broken['channels'][target]['enabled'] = True
    request('/api/config','PUT',broken)
    time.sleep(6)
    fault = request('/api/status')
    assert fault['channels'][target]['state'] == 'reconnecting'
    assert all(fault['channels'][i]['detected'] > baseline['channels'][i]['detected'] for i in others)
    assert any(row['kind'] == 'video_fault' for row in request(f'/api/events?channel={target}'))
    request('/api/config','PUT',original)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        restored = request('/api/status')
        if restored['channels'][target]['state'] == 'running' and restored['channels'][target]['detect_fps'] > 0:
            break
        time.sleep(.5)
    else:
        raise AssertionError('Channel did not recover')
    with urllib.request.urlopen(args.url + '/stream/mosaic.mjpg',timeout=5) as response:
        data = response.read(100000)
        assert b'Content-Type: image/jpeg' in data and b'\xff\xd8' in data
    assert request('/api/events?limit=1')[0]['id'] > 0
    print('PASS invalid config, channel stop, fault event, isolation, recovery, MJPEG and history')
finally:
    request('/api/config','PUT',original)
