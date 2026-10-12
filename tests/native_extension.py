#!/usr/bin/env python3
"""Prove the native escape path with an actual compiled C GDExtension on macOS."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from integration import ROOT, check
sys.path.insert(0, str(ROOT / 'tools'))
import engine


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--godot', required=True)
    args = parser.parse_args()
    project = Path(tempfile.mkdtemp(prefix='native-', dir=ROOT / '.scratch'))
    for name in ['addons', 'examples']:
        shutil.copytree(ROOT / name, project / name)
    shutil.copy(ROOT / 'project.godot', project)
    native = project / 'native'
    native.mkdir()
    # Use the installed engine's exact ABI; do not vendor an independently drifting header.
    subprocess.run([args.godot, '--headless', '--dump-gdextension-interface'], cwd=native, check=True, capture_output=True)
    result = project / 'native-result.txt'
    source = '''#include "gdextension_interface.h"
#include <stdio.h>
#include <stdint.h>
static void initialize(void *data, GDExtensionInitializationLevel level) {
    if (level != GDEXTENSION_INITIALIZATION_SCENE) return;
    uint64_t sum = 0;
    for (uint64_t i=1; i<=1000000; ++i) sum += i;
    FILE *file = fopen(RESULT_PATH, "w");
    if (file) { fprintf(file, "%llu", (unsigned long long)sum); fclose(file); }
}
static void deinitialize(void *data, GDExtensionInitializationLevel level) {}
GDExtensionBool repl_native_init(GDExtensionInterfaceGetProcAddress get_proc,
    GDExtensionClassLibraryPtr library, GDExtensionInitialization *init) {
    init->minimum_initialization_level = GDEXTENSION_INITIALIZATION_SCENE;
    init->userdata = NULL;
    init->initialize = initialize;
    init->deinitialize = deinitialize;
    return 1;
}
'''.replace('RESULT_PATH', json.dumps(str(result)))
    (native / 'probe.c').write_text(source)
    subprocess.run(['xcrun', 'clang', '-dynamiclib', '-o', str(native/'probe.dylib'), str(native/'probe.c')], check=True)
    # Create the descriptor after startup so the test exercises loading through REPL.
    with (project/'import.log').open('w') as output:
        subprocess.run([args.godot, '--headless', '--editor', '--import', '--path', str(project)], stdout=output, stderr=subprocess.STDOUT, check=True, timeout=60)
    launch = engine.start(project, args.godot, 'runtime', True)
    try:
        assert not result.exists()
        (native / 'probe.gdextension').write_text('''[configuration]
entry_symbol="repl_native_init"
compatibility_minimum="4.5"
[libraries]
macos="res://native/probe.dylib"
''')
        response = check(project, 'runtime', 'return GDExtensionManager.load_extension("res://native/probe.gdextension")')
        assert response['value'] == 0, response
        assert result.read_text() == '500000500000'
        response = check(project, 'runtime', 'return FileAccess.get_file_as_string("res://native-result.txt")')
        assert response['value'] == '500000500000'
        assert check(project, 'runtime', 'return GDExtensionManager.unload_extension("res://native/probe.gdextension")')['value'] == 0
        report = {'status':'PASS','evidence':str(project),'checks':['compile C against engine-generated ABI','load native code via REPL','verify native computed result via filesystem and REPL','unload extension']}
        (ROOT/'.scratch/native-test.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
    finally:
        engine.stop(project, launch['id'])


if __name__ == '__main__':
    main()
