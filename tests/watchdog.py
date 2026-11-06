"""Verify a hung inference call triggers one supervised restart and HDMI reconnects."""
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request
import sys

ROOT=Path(__file__).resolve().parents[1]
RUNTIME=ROOT/'runtime'
sys.path.insert(0,str(RUNTIME/'tools'))
from hdmi_health import check as preview_health
marker=RUNTIME/'data/inject-inference-hang'
active=[c['id'] for c in json.loads((RUNTIME/'config.json').read_text())['channels'] if c['enabled']]
environment=dict(os.environ,LD_PRELOAD=str(ROOT/'build/libtest_faults.so'),MVAI_HANG_INFERENCE_FILE=str(marker))


def control(action,env=None):
    output=subprocess.check_output(['python3','tools/control.py',action],cwd=RUNTIME,env=env,text=True)
    return json.loads(output)


def status():
    with urllib.request.urlopen('http://127.0.0.1:8090/api/status',timeout=3) as response: return json.load(response)


control('stop')
report={'passed':False}
try:
    initial=control('start',environment)
    subprocess.run(['python3','tools/hdmi.py','--restart','--fullscreen'],cwd=RUNTIME,check=True)
    player=json.loads((RUNTIME/'data/hdmi-preview.json').read_text())['placement']['player_pid']
    marker.touch()
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        try:
            current=status()
            if current['process_id']!=initial['pid'] and all(current['channels'][i]['detected']>30 for i in active): break
        except OSError:
            pass
        time.sleep(.5)
    else: raise AssertionError('Watchdog restart did not recover enabled channels')
    state=control('status')
    assert state['restarts']==1
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        try:
            preview=json.loads((RUNTIME/'data/hdmi-preview.json').read_text())
            recovered_player=preview['placement']['player_pid']
            if Path('/proc/{}/exe'.format(recovered_player)).resolve().name=='ffplay': break
        except (OSError,KeyError):
            pass
        time.sleep(.2)
    else: raise AssertionError('HDMI did not recover after watchdog restart')
    health=preview_health(3)
    assert recovered_player!=player, 'Watchdog must replace the old HDMI player'
    assert health['pixels_changed'] and health['cpu_ticks_advanced']>0, 'Recovered HDMI window is not refreshing'
    report={'passed':True,'old_pid':initial['pid'],'new_pid':current['process_id'],'restarts':state['restarts'],
            'old_hdmi_player_pid':player,'hdmi_player_pid':recovered_player,'hdmi_recovered':True,
            'display_health':health,'status_after_restart':current}
    print('PASS hung inference watchdog, supervised restart, enabled-channel recovery and HDMI recovery',flush=True)
finally:
    if marker.exists(): marker.unlink()
    control('stop')
    control('start')
    subprocess.run(['python3','tools/hdmi.py','--fullscreen'],cwd=RUNTIME,check=True)
    (RUNTIME/'data/watchdog-test.json').write_text(json.dumps(report,indent=2))
