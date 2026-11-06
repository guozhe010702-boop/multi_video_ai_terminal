"""Verify age, size and interrupted-write cleanup in an isolated event store."""
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1] / 'runtime'
folder = ROOT / 'data-retention-test'
if folder.exists():
    raise SystemExit('Use a fresh data-retention-test directory for this test')
snapshots = folder / 'snapshots'
snapshots.mkdir(parents=True)
source = next((ROOT / 'data/snapshots').glob('*.jpg'))
connection = sqlite3.connect(str(folder / 'events.sqlite3'))
connection.execute('CREATE TABLE events(id INTEGER PRIMARY KEY,at REAL NOT NULL,channel INTEGER NOT NULL,kind TEXT NOT NULL,message TEXT NOT NULL,snapshot TEXT NOT NULL,details TEXT NOT NULL)')
fixture_count = max(24, 2 * 1024 * 1024 // source.stat().st_size + 1)
for index in range(fixture_count):
    filename = str(1000000 + index) + '-0.jpg'
    shutil.copy2(source,snapshots/filename)
    stamp = time.time()-172800 if index == 0 else time.time()
    connection.execute('INSERT INTO events(at,channel,kind,message,snapshot,details) VALUES(?,0,?,?,?,?)',
                       (stamp,'target','retention fixture',filename,'{}'))
connection.commit()
connection.close()
shutil.copy2(source,snapshots/'orphan.jpg')
(snapshots/'pending.jpg.tmp').write_bytes(b'incomplete')
config = json.loads((ROOT/'config.json').read_text())
config.update(data_dir='data-retention-test',retention_days=1,snapshot_limit_mb=1,port=8091)
for channel in config['channels']: channel['enabled']=False
config_path = ROOT/'retention-test.json'
config_path.write_text(json.dumps(config))
process = None
try:
    with (folder/'test.log').open('ab') as log:
        process = subprocess.Popen([str(ROOT/'bin/multi_video_ai_terminal'),'--config',str(config_path)],cwd=ROOT,stdout=log,stderr=log)
    deadline = time.monotonic()+20
    while time.monotonic()<deadline:
        if process.poll() is not None: raise AssertionError('Retention terminal exited')
        try:
            with urllib.request.urlopen('http://127.0.0.1:8091/api/events?limit=100',timeout=2) as response: rows=json.load(response)
            size=sum(p.stat().st_size for p in snapshots.glob('*.jpg'))
            if size<=1024*1024 and not (snapshots/'1000000-0.jpg').exists(): break
        except OSError:
            pass
        time.sleep(.5)
    else: raise AssertionError('Retention cleanup timed out')
    assert len(rows)<fixture_count
    assert not (snapshots/'orphan.jpg').exists()
    assert not (snapshots/'pending.jpg.tmp').exists()
    assert all(row['snapshot'] for row in rows)
    report={'passed':True,'remaining_events':len(rows),'remaining_snapshot_bytes':size,
            'expired_removed':True,'orphan_removed':True,'temporary_removed':True}
    (ROOT/'data/retention-test.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report),flush=True)
finally:
    if process and process.poll() is None:
        process.terminate()
        process.wait(timeout=20)
