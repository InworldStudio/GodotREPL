#!/usr/bin/env python3
"""Exercise the public process CLI and prove it leaves an unrelated Godot alive."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from integration import ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--godot', required=True)
    args = parser.parse_args()
    project = Path(tempfile.mkdtemp(prefix='lifecycle-', dir=ROOT/'.scratch'))
    for name in ['addons', 'examples']:
        shutil.copytree(ROOT/name, project/name)
    shutil.copy(ROOT/'project.godot', project)
    with (project/'import.log').open('w') as output:
        subprocess.run([args.godot, '--headless', '--editor', '--import', '--path', str(project)], stdout=output, stderr=subprocess.STDOUT, check=True, timeout=60)
    def cli(*arguments, expected=0):
        result = subprocess.run(['python3',str(ROOT/'tools/engine.py'),'--project',str(project),*arguments], text=True, capture_output=True, timeout=60)
        assert result.returncode == expected, result
        return json.loads(result.stdout if expected==0 else result.stderr)
    env = dict(os.environ)
    env.pop('GODOT_REPL_ENABLE', None)
    owned = []
    with (project/'unmanaged.log').open('w') as output:
        unrelated = subprocess.Popen([args.godot,'--headless','--path',str(project)],env=env,stdout=output,stderr=subprocess.STDOUT)
        try:
            started = cli('start','--godot',args.godot,'--headless')
            owned.append(started['id'])
            unknown = cli('stop','0'*32,expected=2)
            assert 'Unknown launch' in unknown['message']
            assert unrelated.poll() is None
            restarted = cli('restart',started['id'])
            owned.append(restarted['id'])
            assert restarted['id'] != started['id'] and restarted['instance'] != started['instance']
            assert restarted['previous']['state']=='stopped'
            # Startup expires before registration. The supervisor must still stop its own child.
            failed = cli('--timeout','0.001','start','--godot',args.godot,'--headless',expected=2)
            assert 'stopped owned launch' in failed['message'], failed
            statuses = cli('status')
            assert [s['id'] for s in statuses if s['state']=='running'] == [restarted['id']], statuses
            assert cli('stop',restarted['id'])['state']=='stopped'
            assert cli('stop',restarted['id'])['state']=='stopped'
            assert unrelated.poll() is None
            report={'status':'PASS','evidence':str(project),'checks':['public CLI start/status/restart/stop','unknown launch refused','startup timeout cleans owned child','idempotent stop','unmanaged Godot remains alive']}
            (ROOT/'.scratch/lifecycle-test.json').write_text(json.dumps(report,indent=2))
            print(json.dumps(report,indent=2))
        finally:
            for launch in owned:
                cli('stop',launch)
            unrelated.terminate()
            unrelated.wait(timeout=5)


if __name__=='__main__':
    main()
