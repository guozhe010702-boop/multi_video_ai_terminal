"""Live mixed-input, local loop, SPS resolution-change and corrupt-file regression."""
import copy
import json
from pathlib import Path
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
URL='http://127.0.0.1:8090'


def request(path,method='GET',data=None):
    body=None if data is None else json.dumps(data).encode()
    req=urllib.request.Request(URL+path,data=body,method=method,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=15) as response: return json.load(response)


original=request('/api/config')
active=[c['id'] for c in original['channels'] if c['enabled']]
target=max(active)
try:
    mixed=copy.deepcopy(original)
    mixed['channels'][target].update(source=str(ROOT/'fixtures/resolution-change.h264'),enabled=True,loop=True)
    request('/api/config','PUT',mixed)
    baseline=request('/api/status')
    widths=set()
    generations=set()
    samples=[]
    deadline=time.monotonic()+18
    while time.monotonic()<deadline:
        status=request('/api/status')
        c=status['channels'][target]
        if c['state']=='running':
            widths.add(c['width'])
            generations.add(c['generation'])
        samples.append(status)
        time.sleep(.2)
    assert 1920 in widths and 1280 in widths, 'Dynamic SPS resolution change was not observed'
    assert len(generations)>=2, 'Local video did not loop'
    assert all(samples[-1]['channels'][i]['detected']>baseline['channels'][i]['detected'] for i in active)
    mixed['channels'][target]['source']=str(ROOT/'fixtures/corrupt.mp4')
    request('/api/config','PUT',mixed)
    time.sleep(3)
    failed=request('/api/status')
    assert failed['channels'][target]['state']=='reconnecting'
    assert failed['channels'][target]['error']
    result={'passed':True,'widths':sorted(widths),'loop_generations':sorted(generations),
            'corrupt_file_error':failed['channels'][target]['error'],'last_mixed_status':samples[-1]}
    (ROOT/'runtime/data/mixed-sources.json').write_text(json.dumps(result,indent=2))
    print('PASS RTSP plus local file, realtime looping, dynamic 1080p/720p SPS and corrupt-file isolation',flush=True)
finally:
    request('/api/config','PUT',original)
