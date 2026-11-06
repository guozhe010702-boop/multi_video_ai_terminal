"""Exercise real ENOSPC and one RKNN error through an explicitly preloaded test shim."""
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'runtime'
output = RUNTIME / 'data/fault-injection.json'
active=[c['id'] for c in json.loads((RUNTIME/'config.json').read_text())['channels'] if c['enabled']]
snapshot_marker = RUNTIME / 'data/inject-snapshot-failure'
inference_marker = RUNTIME / 'data/inject-inference-failure'


def read(path):
    with urllib.request.urlopen('http://127.0.0.1:8090' + path, timeout=5) as response:
        return json.load(response)


def wait(predicate, seconds=60):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            result = predicate()
            if result:
                return result
        except OSError:
            pass
        time.sleep(.2)
    raise AssertionError('Fault injection condition timed out')


def storage_failed():
    status = read('/api/status')
    return status if status['storage']['error'] else None


def storage_ready():
    status = read('/api/status')
    return status if not status['storage']['error'] and all(status['channels'][i]['detected'] > 0 for i in active) else None


subprocess.run(['python3','tools/control.py','stop'],cwd=RUNTIME,check=True)
environment = dict(os.environ, LD_PRELOAD=str(ROOT/'build/libtest_faults.so'),
                   MVAI_FAIL_SNAPSHOT_FILE=str(snapshot_marker), MVAI_FAIL_INFERENCE_FILE=str(inference_marker))
started = time.time()
report = {'started':started,'passed':False}
process = None
try:
    snapshot_marker.touch()
    with (RUNTIME/'data/fault-injection.log').open('ab') as log:
        process = subprocess.Popen([str(RUNTIME/'bin/multi_video_ai_terminal'),'--config',str(RUNTIME/'config.json')],
                                   cwd=RUNTIME,env=environment,stdout=log,stderr=log)
    initial = wait(lambda: read('/api/status'))
    failed = wait(storage_failed)
    assert 'Snapshot write failed' in failed['storage']['error']
    assert all(failed['channels'][i]['detected'] > 0 for i in active)
    report['storage_error'] = failed['storage']['error']
    report['detection_during_storage_failure'] = [c['detected'] for c in failed['channels']]
    snapshot_marker.unlink()
    restored = wait(storage_ready)
    report['storage_recovered'] = True
    inference_marker.touch()
    fault = wait(lambda: next((row for row in read('/api/events?limit=100') if row['kind']=='inference_fault' and row['at']>=started),None))
    recovered = wait(lambda: next((row for row in read('/api/events?limit=100')
                                  if row['kind']=='recovered' and row['channel']==fault['channel'] and row['at']>=fault['at']),None))
    final = read('/api/status')
    assert all(final['channels'][i]['detected'] > restored['channels'][i]['detected'] for i in active)
    report.update(passed=True,inference_fault=fault,inference_recovered=recovered,final=final)
    print('PASS snapshot ENOSPC, continued detection, storage recovery and RKNN context recovery',flush=True)
finally:
    for path in (snapshot_marker,inference_marker):
        if path.exists(): path.unlink()
    if process and process.poll() is None:
        process.terminate()
        try: process.wait(timeout=20)
        except subprocess.TimeoutExpired: process.kill();process.wait()
    output.write_text(json.dumps(report,indent=2))
    subprocess.run(['python3','tools/control.py','start'],cwd=RUNTIME,check=True)
