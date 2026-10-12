#!/usr/bin/env python3
"""Own Godot child processes outside the engine, so even a blocked main thread can be stopped.

No signals are sent to PIDs discovered from REPL instance files. A private supervisor
retains the actual Popen child handle and accepts stop requests for that launch only.
"""
import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from repl import ReplError, atomic_json, instances

TERMINAL = {'stopped', 'exited', 'failed'}


def directory(project, launch):
    if len(launch) != 32 or any(c not in '0123456789abcdef' for c in launch):
        raise ReplError('Invalid managed launch ID')
    return Path(project).resolve() / '.godot/godot_repl/managed' / launch


def read_status(path):
    try:
        return json.loads((path / 'status.json').read_text())
    except FileNotFoundError:
        return {'state': 'starting'}


def start(project, godot, channel='runtime', headless=False, timeout=45):
    if not math.isfinite(timeout) or timeout <= 0:
        raise ReplError('Startup timeout must be positive and finite')
    if channel not in ('editor', 'runtime'):
        raise ReplError('Channel must be editor or runtime')
    project = Path(project).resolve()
    godot = Path(godot).resolve()
    if not (project / 'project.godot').is_file() or not godot.is_file():
        raise ReplError('Existing project.godot and Godot executable required')
    launch = uuid.uuid4().hex
    path = directory(project, launch)
    path.mkdir(parents=True, mode=0o700)
    command = [str(godot), '--path', str(project)]
    if headless:
        command.append('--headless')
    if channel == 'editor':
        command.append('--editor')
    atomic_json(path / 'launch.json', {'id': launch, 'project': str(project),
                'godot': str(godot), 'channel': channel, 'headless': headless, 'command': command})
    with (path / 'supervisor.log').open('w') as output:
        supervisor = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                                       '--project', str(project), '_worker', launch],
                                      stdout=output, stderr=subprocess.STDOUT,
                                      stdin=subprocess.DEVNULL, start_new_session=True)
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            status = read_status(path)
            if status['state'] in TERMINAL:
                raise ReplError(f'Launch {launch}: {status}; see {path}')
            for _, descriptor in instances(project, channel):
                if descriptor.get('managed_id') == launch and descriptor['pid'] == status.get('pid'):
                    return dict(status, instance=descriptor['instance'], log=str(path / 'godot.log'))
            if supervisor.poll() is not None:
                raise ReplError(f'Supervisor exited for {launch}; see {path}')
            time.sleep(.1)
    except KeyboardInterrupt:
        stop(project, launch)
        raise ReplError(f'Startup cancelled; stopped owned launch {launch}')
    stop(project, launch)
    raise ReplError(f'Godot did not expose {channel} within {timeout}s; stopped owned launch {launch}; see {path}')


def stop(project, launch, timeout=12):
    path = directory(project, launch)
    if not (path / 'launch.json').exists():
        raise ReplError('Unknown launch; unmanaged processes are never terminated')
    status = read_status(path)
    if status['state'] in TERMINAL:
        return status
    # Idempotent: the worker removes the request only after the child has stopped.
    (path / 'stop').touch(mode=0o600)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = read_status(path)
        if status['state'] in TERMINAL:
            return status
        time.sleep(.1)
    raise ReplError(f'Supervisor unresponsive for {launch}; no unverified PID was killed')


def restart(project, launch, timeout=45):
    path = directory(project, launch)
    config = json.loads((path / 'launch.json').read_text())
    previous = stop(project, launch)
    current = start(project, config['godot'], config['channel'], config['headless'], timeout)
    return dict(current, previous=previous, recovery='Loaded saved project. Unsaved changes and in-memory sessions are not restored.')


def worker(project, launch):
    path = directory(project, launch)
    config = json.loads((path / 'launch.json').read_text())
    child = None
    status = {'id': launch, 'channel': config['channel'], 'state': 'starting',
              'supervisor_pid': os.getpid(), 'project': str(Path(project).resolve())}
    try:
        with (path / 'godot.log').open('w') as output:
            child = subprocess.Popen(config['command'], stdin=subprocess.DEVNULL,
                                     stdout=output, stderr=subprocess.STDOUT,
                                     env=dict(os.environ, GODOT_REPL_ENABLE='1', GODOT_REPL_MANAGED_ID=launch))
            status.update(state='running', pid=child.pid)
            while child.poll() is None:
                status['heartbeat'] = time.time()
                atomic_json(path / 'status.json', status)
                if (path / 'stop').exists():
                    child.terminate()
                    try:
                        child.wait(timeout=3)
                        status['forced'] = False
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=5)
                        status['forced'] = True
                    status['state'] = 'stopped'
                    break
                time.sleep(.2)
            if status['state'] == 'running':
                status['state'] = 'exited'
            status['returncode'] = child.returncode
    except Exception as error:
        status.update(state='failed', error=str(error))
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        status['heartbeat'] = time.time()
        atomic_json(path / 'status.json', status)
        (path / 'stop').unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', default='.')
    parser.add_argument('--timeout', type=float, default=45)
    commands = parser.add_subparsers(dest='command', required=True)
    launch = commands.add_parser('start')
    launch.add_argument('--godot', required=True)
    launch.add_argument('--channel', choices=['editor', 'runtime'], default='runtime')
    launch.add_argument('--headless', action='store_true')
    for name in ['stop', 'restart', '_worker']:
        commands.add_parser(name).add_argument('id')
    commands.add_parser('status')
    args = parser.parse_args()
    try:
        if args.command == '_worker':
            worker(args.project, args.id)
            return 0
        if args.command == 'start':
            result = start(args.project, args.godot, args.channel, args.headless, args.timeout)
        elif args.command == 'stop':
            result = stop(args.project, args.id)
        elif args.command == 'restart':
            result = restart(args.project, args.id, args.timeout)
        else:
            root = Path(args.project).resolve() / '.godot/godot_repl/managed'
            result = [dict(read_status(p), launch_id=p.name) for p in root.iterdir()] if root.exists() else []
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, ReplError) as error:
        print(json.dumps({'status': 'CONTROL_ERROR', 'message': str(error)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
