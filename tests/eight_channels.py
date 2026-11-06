"""Verify slots 5..8, eight-view JPEG, legacy migration and alarm history routes."""
import argparse
import copy
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--url',default='http://127.0.0.1:8090')
parser.add_argument('--output',type=Path,default=Path('data/eight-functional.json'))
args=parser.parse_args()
def api(path,method='GET',body=None):
    req=urllib.request.Request(args.url+path,data=None if body is None else json.dumps(body).encode(),
        method=method,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=20) as response: return json.load(response)
def frame():
    with urllib.request.urlopen(args.url+'/stream/mosaic.mjpg',timeout=5) as response:
        while True:
            line=response.readline()
            if not line: raise EOFError('MJPEG ended')
            if line.lower().startswith(b'content-length:'):
                size=int(line.split(b':',1)[1]);response.readline();data=response.read(size);break
    assert data[:2]==b'\xff\xd8'
    index=2
    while index<len(data):
        assert data[index]==255
        while data[index]==255: index+=1
        marker=data[index];index+=1
        length=int.from_bytes(data[index:index+2],'big')
        if marker in (192,193,194): return int.from_bytes(data[index+5:index+7],'big'),int.from_bytes(data[index+3:index+5],'big')
        index+=length
    raise AssertionError('JPEG dimensions not found')
def wait_inputs(count):
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        value=api('/api/status')
        selected=[c for c in value['channels'] if c['enabled']]
        if len(selected)==count and all(c['state']=='running' and c['detect_fps']>0 for c in selected): return value
        time.sleep(.3)
    raise AssertionError('Inputs did not recover')
def dimensions(expected):
    for _ in range(20):
        if frame()==expected: return
        time.sleep(.1)
    raise AssertionError('MJPEG dimensions do not match layout')

original=api('/api/config')
assert len(original['channels'])==8 and all(c['enabled'] for c in original['channels'])
report={'passed':False}
try:
    initial=wait_inputs(8)
    assert initial['max_channels']==8 and initial['layout']['video_tiles']==8
    dimensions((1920,1080))
    subprocess.run([sys.executable,str(Path(__file__).with_name('integration.py')),'--url',args.url],check=True)
    for path,method in (('/api/channels/8/start','POST'),('/api/events?channel=8','GET')):
        try:
            api(path,method);raise AssertionError('Invalid channel was accepted')
        except urllib.error.HTTPError as error: assert error.code in (400,404)
    bad=copy.deepcopy(original);bad['channels'][7]['id']=0
    try:
        api('/api/config','PUT',bad);raise AssertionError('Duplicate channel was accepted')
    except urllib.error.HTTPError as error: assert error.code==400
    assert api('/api/config')==original
    rows=api('/api/events?channel=7&limit=100')
    assert rows and all(row['channel']==7 for row in rows)
    snapshot=next((row['snapshot'] for row in rows if row['snapshot']),None)
    assert snapshot,'Channel eight should have an alarm snapshot with person test scenes'
    with urllib.request.urlopen(args.url+snapshot,timeout=5) as response: assert response.read(2)==b'\xff\xd8'
    legacy=copy.deepcopy(original);legacy['channels']=legacy['channels'][:4]
    api('/api/config','PUT',legacy)
    quad=wait_inputs(4);dimensions((1280,720))
    expanded=api('/api/config')
    assert len(expanded['channels'])==8 and all(not c['enabled'] and not c['source'] for c in expanded['channels'][4:])
    api('/api/config','PUT',original)
    restored=wait_inputs(8);dimensions((1920,1080))
    assert all(restored['channels'][i]['generation']>initial['channels'][i]['generation'] for i in range(4,8))
    report.update(passed=True,initial=initial,legacy_layout=quad['layout'],restored=restored,
                  channel8_snapshot=snapshot,api_regression=True)
finally:
    api('/api/config','PUT',original)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2),encoding='utf-8')
print('PASS eight-channel APIs, snapshot, isolation and live four/eight preview switching')
