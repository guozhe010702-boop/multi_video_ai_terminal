"""Start, supervise and stop only this project's process group."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'data/process.json'


def identity(pid):
    try:
        executable = Path(os.readlink('/proc/{}/exe'.format(pid)))
        ticks = Path('/proc/{}/stat'.format(pid)).read_text().rsplit(')', 1)[1].split()[19]
        return str(executable), ticks
    except (OSError, IndexError):
        return None


def supervisor_alive(state):
    pid = state.get('supervisor_pid')
    if not pid:
        return False
    current = identity(pid)
    if not current or (state.get('supervisor_start_ticks') is not None and current[1] != state['supervisor_start_ticks']):
        return False
    try:
        command = Path('/proc/{}/cmdline'.format(pid)).read_bytes().split(b'\0')
        cwd = Path('/proc/{}/cwd'.format(pid)).resolve()
        script_matches = str(Path(__file__).resolve()).encode() in command or (b'tools/control.py' in command and cwd == ROOT)
        return script_matches and b'_supervise' in command
    except OSError:
        return False


def status():
    try:
        state = json.loads(STATE.read_text())
        expected = (str(ROOT / 'bin/multi_video_ai_terminal'), state['start_ticks'])
        state['running'] = identity(state['pid']) == expected
        state['supervisor_running'] = supervisor_alive(state)
        return state
    except (OSError, ValueError, KeyError):
        return {'running': False, 'supervisor_running': False}


def supervise():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    process = None
    restore_preview = False
    fullscreen = False
    try:
        for attempt in range(4):
            with (ROOT / 'data/terminal.log').open('ab') as log:
                process = subprocess.Popen([str(ROOT / 'bin/multi_video_ai_terminal'), '--config', str(ROOT / 'config.json')],
                                           cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
            current = None
            for _ in range(100):
                current = identity(process.pid)
                if current or process.poll() is not None:
                    break
                time.sleep(.01)
            if current:
                temporary = STATE.with_suffix('.tmp')
                temporary.write_text(json.dumps({'pid': process.pid, 'supervisor_pid': os.getpid(), 'start_ticks': current[1],
                                                 'supervisor_start_ticks': identity(os.getpid())[1], 'restarts': attempt}))
                temporary.replace(STATE)
            if restore_preview and current:
                config = json.loads((ROOT / 'config.json').read_text())
                for _ in range(100):
                    try:
                        with urllib.request.urlopen('http://127.0.0.1:{}/api/status'.format(config['port']),timeout=1) as response:
                            if json.load(response).get('process_id') == process.pid:
                                break
                    except OSError:
                        pass
                    if process.poll() is not None:
                        break
                    time.sleep(.1)
                if process.poll() is None:
                    with (ROOT / 'data/terminal.log').open('ab') as log:
                        try:
                            subprocess.run([sys.executable,str(ROOT/'tools/hdmi.py'),'--restart','--fullscreen' if fullscreen else '--windowed'],
                                           cwd=ROOT,stdout=log,stderr=log,timeout=40)
                        except subprocess.TimeoutExpired:
                            log.write(b'HDMI restore timed out\n')
            code = process.wait()
            if code != 75:
                return
            try:
                preview = json.loads((ROOT/'data/hdmi-preview.json').read_text())
                restore_preview = bool(preview.get('pid'))
                fullscreen = preview.get('fullscreen',False)
            except (OSError,ValueError):
                restore_preview = False
            time.sleep(1)
    finally:
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('start', 'stop', 'status', '_supervise'))
    args = parser.parse_args()
    (ROOT / 'data').mkdir(exist_ok=True)
    if args.action == '_supervise':
        supervise()
        return
    with (ROOT / 'data/control.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = status()
        if args.action == 'start' and not state['running']:
            process = None
            if not state['supervisor_running']:
                process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '_supervise'], cwd=ROOT,
                                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            for _ in range(200):
                time.sleep(.1)
                state = status()
                if state['running']:
                    config = json.loads((ROOT / 'config.json').read_text())
                    try:
                        with urllib.request.urlopen('http://127.0.0.1:{}/api/status'.format(config['port']), timeout=1) as response:
                            payload = json.load(response)
                            if payload.get('process_id') != state['pid']:
                                raise OSError('HTTP service belongs to another process')
                        break
                    except OSError:
                        continue
                if process is not None and process.poll() is not None:
                    raise RuntimeError('Terminal exited, read data/terminal.log')
            else:
                raise RuntimeError('Startup timed out, inspect data/terminal.log and status')
        elif args.action == 'stop' and (state['running'] or state['supervisor_running']):
            supervisor = state.get('supervisor_pid')
            if not supervisor_alive(state):
                raise RuntimeError('Supervisor identity mismatch')
            os.kill(supervisor, signal.SIGTERM)
            for _ in range(220):
                current = status()
                if not current['running'] and not current['supervisor_running']:
                    break
                time.sleep(.1)
            else:
                raise RuntimeError('Stop timed out, inspect process before retrying')
            state = status()
        print(json.dumps(state))


if __name__ == '__main__':
    main()
