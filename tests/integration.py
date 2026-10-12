#!/usr/bin/env python3
"""Run a real Godot editor and game, using a disposable project copy."""
import argparse
import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('repl', ROOT / 'tools/repl.py')
repl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repl)


def wait_for(project, channel, process, seconds=30):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if process.poll() is not None:
            raise AssertionError(f'{channel} exited: {process.returncode}')
        if repl.instances(project, channel):
            return
        time.sleep(.1)
    raise AssertionError(f'{channel} did not publish an instance')


def check(project, channel, code='', status='OK', mode='block', **kwargs):
    response = repl.execute(project, channel, code, mode, **kwargs)
    assert response['status'] == status, response
    return response


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--godot', required=True)
    args = parser.parse_args()
    scratch = ROOT / '.scratch'
    scratch.mkdir(exist_ok=True)
    project = Path(tempfile.mkdtemp(prefix='integration-', dir=scratch))
    shutil.copytree(ROOT / 'addons', project / 'addons')
    shutil.copytree(ROOT / 'examples', project / 'examples')
    shutil.copy(ROOT / 'project.godot', project)
    processes, logs = [], []
    checks = []
    try:
        with (project / 'import.log').open('w') as output:
            subprocess.run([args.godot, '--headless', '--path', str(project), '--editor', '--import'],
                           stdout=output, stderr=subprocess.STDOUT, timeout=60, check=True)
        env = dict(os.environ, GODOT_REPL_ENABLE='1')
        for channel in ['editor', 'runtime']:
            output = (project / f'{channel}.log').open('w')
            logs.append(output)
            command = [args.godot, '--headless', '--path', str(project)]
            if channel == 'editor':
                command += ['--editor', 'res://examples/lab.tscn']
            process = subprocess.Popen(command, env=env, stdout=output, stderr=subprocess.STDOUT)
            processes.append(process)
            wait_for(project, channel, process)
            assert check(project, channel, '1 + 2', mode='expr')['value'] == 3
            for expected in [1, 2]:
                assert check(project, channel, (ROOT / 'examples/state.gd.txt').read_text())['value'] == expected
            assert check(project, channel, 'ctx.state.size()', mode='expr', session='other')['value'] == 0
            assert check(project, channel, 'var sum = 0\nfor i in range(5):\n\tsum += i\nreturn sum')['value'] == 10
            assert check(project, channel, 'return null')['value'] is None
            full_script = '@tool\nextends RefCounted\nfunc run(ctx):\n\tctx.state["helper"] = self\n\treturn twice(3)\nfunc twice(n):\n\treturn n * 2\n'
            assert check(project, channel, full_script, mode='script')['value'] == 6
            assert check(project, channel, 'ctx.state["helper"].twice(4)', mode='expr')['value'] == 8
            check(project, channel, '@tool\nextends RefCounted', mode='script', status='INVALID_SCRIPT')
            check(project, channel, '@tool\nextends RefCounted\nfunc run():\n\treturn 1', mode='script', status='INVALID_SCRIPT')
            check(project, channel, '@tool\nextends RefCounted\nfunc run(ctx: int):\n\treturn 1', mode='script', status='INVALID_SCRIPT')
            check(project, channel, '@tool\nextends RefCounted\nfunc _init(x):\n\tpass\nfunc run(ctx):\n\treturn 1', mode='script', status='INVALID_SCRIPT')
            check(project, channel, '@tool\nextends Node\nfunc run(ctx):\n\treturn 1', mode='script', status='INVALID_SCRIPT')
            check(project, channel, 'var = broken', status='COMPILE_ERROR')
            check(project, channel, 'var n = null\nreturn n.missing()', status='RUNTIME_ERROR')
            check(project, channel, 'assert(false, "repl test")\nreturn 7', status='RUNTIME_ERROR')
            response = check(project, channel, 'print("captured")\nreturn 42')
            assert any('captured' in x['text'] for x in response['output']), response
            control = check(project, channel, 'print(String.chr(27) + "[32mgreen")\nreturn String.chr(1)')
            assert control['value'] == chr(1)
            assert check(project, channel, 'await ctx.frames(3)\nreturn 9')['value'] == 9
            check(project, channel, 'await ctx.sleep(2)\nreturn 1', status='TIMEOUT', timeout=.1)
            time.sleep(.1)
            assert check(project, channel, '5', mode='expr')['value'] == 5
            check(project, channel, 'ctx.state["x"] = 3', session='reset-me')
            check(project, channel, op='reset', session='reset-me')
            assert check(project, channel, 'ctx.state.size()', mode='expr', session='reset-me')['value'] == 0
            # An arbitrary await cannot be forcibly cancelled: keep the host busy.
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(check, project, channel,
                                     'await ctx.tree.create_timer(.7).timeout\nreturn 2',
                                     status='TIMEOUT', timeout=.1)
                future.result()
                check(project, channel, '3', mode='expr', status='BUSY')
            time.sleep(.8)
            assert check(project, channel, '3', mode='expr')['value'] == 3
            checks.append(f'{channel}: eval, state, isolation, loops, null, compile/runtime/assert errors, output, await, timeout, busy, reset')
        assert check(project, 'runtime', (ROOT / 'examples/runtime.gd.txt').read_text())['value']['type'] == 'Vector3'
        check(project, 'runtime', 'ctx.state["probe"] = ctx.tree.root.get_node("Laboratory/Probe")')
        assert check(project, 'runtime', 'ctx.state["probe"].position.x', mode='expr')['value'] == 3
        check(project, 'editor', 'ctx.editor.open_scene_from_path("res://examples/lab.tscn")\nawait ctx.frames(3)')
        edited = check(project, 'editor', (ROOT / 'examples/editor.gd.txt').read_text())
        assert edited['value'] == 'ReplCreated', edited
        saved = check(project, 'editor', 'var packed = PackedScene.new()\nvar result = packed.pack(ctx.editor.get_edited_scene_root())\nif result != OK:\n\treturn result\nreturn ResourceSaver.save(packed, "res://examples/saved.tscn")')
        assert saved['value'] == 0, saved
        assert 'ReplCreated' in (project / 'examples/saved.tscn').read_text()
        checks.append('runtime live-object state and mutation; editor scene edit/save')
        # CLI itself, not only imported functions.
        completed = subprocess.run(['python3', str(ROOT / 'tools/repl.py'), '--project', str(project),
                                    'eval', '-e', '6*7'], text=True, capture_output=True)
        assert completed.returncode == 0 and json.loads(completed.stdout)['value'] == 42, completed
        failed = subprocess.run(['python3', str(ROOT / 'tools/repl.py'), '--project', str(project),
                                 'eval', '-c', 'var = invalid'], text=True, capture_output=True)
        assert failed.returncode == 1 and json.loads(failed.stdout)['status'] == 'COMPILE_ERROR', failed
        shell = subprocess.run(['python3', str(ROOT / 'tools/repl.py'), '--project', str(project), 'shell'],
                               input='6*7\n:paste\nctx.state["interactive"] = 5\nreturn ctx.state["interactive"]\n:end\n:quit\n',
                               text=True, capture_output=True, timeout=10)
        assert shell.returncode == 0 and '"value": 42' in shell.stdout and '"value": 5' in shell.stdout, shell
        checks.append('CLI success/failure exit codes and interactive shell multiline input')
        directory, descriptor = repl.select_instance(project, 'runtime')
        # Invalid wire data must not execute anything.
        import uuid
        rid = uuid.uuid4().hex
        repl.atomic_json(directory / 'requests' / f'{rid}.json', {'id': rid, 'protocol': 99})
        response_path = directory / 'responses' / f'{rid}.json'
        until = time.monotonic() + 3
        while not response_path.exists() and time.monotonic() < until:
            time.sleep(.02)
        assert json.loads(response_path.read_text())['status'] == 'INVALID_REQUEST'
        response_path.unlink()
        # Cancel a known request after its code has started (observable state).
        rid = uuid.uuid4().hex
        repl.atomic_json(directory / 'requests' / f'{rid}.json', {
            'protocol': 1, 'id': rid, 'op': 'eval', 'mode': 'block', 'session': 'cancel',
            'code': 'await ctx.sleep(4)\nreturn 1', 'timeout_ms': 5000,
            'expires_at': time.time() + 8})
        until = time.monotonic() + 3
        while (directory / 'requests' / f'{rid}.json').exists() and time.monotonic() < until:
            time.sleep(.01)
        (directory / 'requests' / f'{rid}.cancel').touch()
        response_path = directory / 'responses' / f'{rid}.json'
        while not response_path.exists() and time.monotonic() < until:
            time.sleep(.02)
        assert json.loads(response_path.read_text())['status'] == 'CANCELLED'
        response_path.unlink()
        time.sleep(.1)
        rid = uuid.uuid4().hex
        repl.atomic_json(directory / 'requests' / f'{rid}.json', {
            'protocol': 1, 'id': rid, 'op': 'eval', 'code': 'ctx.state["bad"] = true',
            'expires_at': time.time() - 1})
        response_path = directory / 'responses' / f'{rid}.json'
        until = time.monotonic() + 3
        while not response_path.exists() and time.monotonic() < until:
            time.sleep(.02)
        assert json.loads(response_path.read_text())['status'] == 'EXPIRED'
        response_path.unlink()
        checks.append('invalid protocol and explicit cooperative cancellation')
        # A process without opt-in must not expose a runtime listener.
        env_disabled = dict(os.environ)
        env_disabled.pop('GODOT_REPL_ENABLE', None)
        with (project / 'disabled.log').open('w') as out:
            disabled = subprocess.Popen([args.godot, '--headless', '--path', str(project)], env=env_disabled,
                                        stdout=out, stderr=subprocess.STDOUT)
            try:
                time.sleep(1)
                assert disabled.poll() is None
                assert len(repl.instances(project, 'runtime')) == 1
            finally:
                disabled.terminate()
                disabled.wait(timeout=5)
        checks.append('runtime opt-in gating')
        # Multiple live instances require explicit selection.
        with (project / 'second.log').open('w') as out:
            second = subprocess.Popen([args.godot, '--headless', '--path', str(project)], env=env,
                                      stdout=out, stderr=subprocess.STDOUT)
            try:
                until = time.monotonic() + 5
                while len(repl.instances(project, 'runtime')) != 2 and time.monotonic() < until:
                    time.sleep(.1)
                assert len(repl.instances(project, 'runtime')) == 2
                try:
                    repl.select_instance(project, 'runtime')
                    raise AssertionError('Ambiguous selection accepted')
                except repl.ReplError:
                    pass
                assert check(project, 'runtime', '7', mode='expr', instance=descriptor['instance'])['value'] == 7
            finally:
                second.terminate()
                second.wait(timeout=5)
        checks.append('multiple-instance isolation and explicit routing')
        report = {'status': 'PASS', 'checks': checks, 'evidence': str(project)}
        (scratch / 'last-test.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for output in logs:
            output.close()
        print(f'Logs retained in {project}')


if __name__ == '__main__':
    main()
