#!/usr/bin/env python3
"""Rendered smoke test. Starts and closes only its own editor/game processes."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from integration import ROOT, check, wait_for


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--godot', required=True)
    args = parser.parse_args()
    project = Path(tempfile.mkdtemp(prefix='gui-', dir=ROOT / '.scratch'))
    for name in ['addons', 'examples']:
        shutil.copytree(ROOT / name, project / name)
    shutil.copy(ROOT / 'project.godot', project)
    processes, logs = [], []
    try:
        with (project / 'import.log').open('w') as output:
            subprocess.run([args.godot, '--headless', '--editor', '--import', '--path', str(project)],
                           stdout=output, stderr=subprocess.STDOUT, check=True, timeout=60)
        for channel in ['editor', 'runtime']:
            output = (project / f'{channel}.log').open('w')
            logs.append(output)
            command = [args.godot, '--path', str(project)]
            if channel == 'editor':
                command += ['--editor']
            process = subprocess.Popen(command, env=dict(os.environ, GODOT_REPL_ENABLE='1'),
                                       stdout=output, stderr=subprocess.STDOUT)
            processes.append(process)
            wait_for(project, channel, process)
        check(project, 'editor', 'ctx.editor.open_scene_from_path("res://examples/lab.tscn")\nawait ctx.frames(5)')
        response = check(project, 'editor', (ROOT / 'examples/editor.gd.txt').read_text())
        assert response['value'] == 'ReplCreated', response
        response = check(project, 'editor', 'return ctx.editor.save_scene()')
        assert response['value'] == 0, response
        assert 'ReplCreated' in (project / 'examples/lab.tscn').read_text()
        # Compiling REPL code must not register saveable external script resources.
        assert not list((project / '.godot/godot_repl').glob('request_*.gd'))
        for phase in ['before', 'after']:
            if phase == 'after':
                check(project, 'runtime', 'var probe = ctx.tree.root.get_node("Laboratory/Probe")\nprobe.position.x = 2\nprobe.get_node("Mesh").mesh.material.albedo_color = Color(0.8, 0.3, 0.6)')
            target = project / f'{phase}.png'
            code = 'await ctx.frames(4)\nawait RenderingServer.frame_post_draw\nvar image = ctx.tree.root.get_texture().get_image()\nreturn image.save_png(' + json.dumps(str(target)) + ')'
            response = check(project, 'runtime', code)
            assert response['value'] == 0 and target.stat().st_size > 1000, response
        assert (project / 'before.png').read_bytes() != (project / 'after.png').read_bytes()
        report = {'status': 'PASS', 'evidence': str(project), 'checks': [
            'GUI editor open/edit/save scene', 'temporary scripts stay in memory',
            'rendered runtime screenshots before/after live position and material changes']}
        (ROOT / '.scratch/gui-test.json').write_text(json.dumps(report, indent=2))
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
