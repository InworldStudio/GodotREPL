#!/usr/bin/env python3
"""Cross-domain acceptance on a NEW project; all engine operations use generic eval.

No workflow-specific methods are added to the REPL. Preserves logs, generated
resources, batch checkpoints, and a report for independent inspection.
"""
import argparse
import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from integration import ROOT, check, repl
sys.path.insert(0, str(ROOT / 'tools'))
import engine

GAME = '''extends Node2D
var score = 0
func _ready():
    $Button.pressed.connect(func(): score += 20)
    if "--verify" in OS.get_cmdline_user_args():
        verify_export.call_deferred()
func _unhandled_input(event):
    if event.is_action_pressed("repl_jump"):
        score += 1
func verify_export():
    await get_tree().process_frame
    var event = InputEventKey.new()
    event.physical_keycode = KEY_SPACE
    event.pressed = true
    Input.parse_input_event(event)
    await get_tree().process_frame
    await get_tree().process_frame
    var args = OS.get_cmdline_user_args()
    var report = {"score": score, "texture": $Probe/Sprite.texture.get_width(),
        "editor_build": OS.has_feature("editor"),
        "listener_children": get_node("/root/GodotReplRuntime").get_child_count()}
    FileAccess.open(args[args.find("--verify")+1], FileAccess.WRITE).store_string(JSON.stringify(report))
    get_tree().quit(0 if score == 1 and not report.editor_build and report.listener_children == 0 else 9)
'''

CREATE = '''
var root = Node2D.new()
root.name = "World"
root.set_script(load("res://game.gd"))
var probe = Node2D.new()
probe.name = "Probe"
probe.position = Vector2(140, 220)
root.add_child(probe)
probe.owner = root
var sprite = Sprite2D.new()
sprite.name = "Sprite"
sprite.texture = load("res://icon.svg")
probe.add_child(sprite)
sprite.owner = root
var button = Button.new()
button.name = "Button"
button.text = "Add 20"
button.position = Vector2(350, 200)
button.size = Vector2(120, 50)
root.add_child(button)
button.owner = root
var body = StaticBody2D.new()
body.name = "Wall"
body.position = Vector2(500, 200)
root.add_child(body)
body.owner = root
var shape = CollisionShape2D.new()
shape.shape = CircleShape2D.new()
shape.shape.radius = 25
body.add_child(shape)
shape.owner = root
var animation = Animation.new()
animation.length = 0.2
var track = animation.add_track(Animation.TYPE_VALUE)
animation.track_set_path(track, "Probe:position")
animation.track_insert_key(track, 0.0, Vector2(140,220))
animation.track_insert_key(track, 0.2, Vector2(240,220))
var library = AnimationLibrary.new()
library.add_animation("move", animation)
var player = AnimationPlayer.new()
player.name = "AnimationPlayer"
player.add_animation_library("", library)
root.add_child(player)
player.owner = root
var packed = PackedScene.new()
assert(packed.pack(root) == OK)
assert(ResourceSaver.save(packed, "res://world.tscn") == OK)
root.free()
ProjectSettings.set_setting("application/run/main_scene", "res://world.tscn")
var key = InputEventKey.new()
key.physical_keycode = KEY_SPACE
ProjectSettings.set_setting("input/repl_jump", {"deadzone": 0.5, "events": [key]})
assert(ProjectSettings.save() == OK)
ctx.editor.get_resource_filesystem().scan()
await ctx.frames(10)
ctx.editor.open_scene_from_path("res://world.tscn")
await ctx.frames(5)
return ctx.editor.get_edited_scene_root().name
'''

BATCH = '''
DirAccess.make_dir_recursive_absolute("res://batch")
var completed = []
for chunk in range(LIMIT):
    var path = "res://batch/chunk_%02d.tscn" % chunk
    if not FileAccess.file_exists(path):
        var root = Node2D.new()
        root.name = "Chunk%d" % chunk
        for j in range(1000):
            var node = Node2D.new()
            var index = chunk * 1000 + j
            node.name = "Item%d" % index
            node.position = Vector2(index % 100, index / 100)
            node.set_meta("index", index)
            root.add_child(node)
            node.owner = root
        var scene = PackedScene.new()
        assert(scene.pack(root) == OK)
        var temp = "res://batch/pending.tscn"
        assert(ResourceSaver.save(scene, temp) == OK)
        assert(DirAccess.rename_absolute(temp, path) == OK)
        root.free()
    var loaded = load(path).instantiate()
    assert(loaded.get_child_count() == 1000)
    for j in range(1000):
        var node = loaded.get_child(j)
        var index = chunk * 1000 + j
        assert(node.get_meta("index") == index)
        assert(node.position == Vector2(index % 100, index / 100))
    loaded.free()
    completed.append({"chunk": chunk, "sha256": FileAccess.get_sha256(path)})
    var file = FileAccess.open("res://batch/checkpoint.tmp", FileAccess.WRITE)
    file.store_string(JSON.stringify(completed))
    file.close()
    assert(DirAccess.rename_absolute("res://batch/checkpoint.tmp", "res://batch/checkpoint.json") == OK)
    if not await ctx.frames(1):
        return {"cancelled": true, "chunks": completed.size()}
return {"chunks": completed.size(), "verified_nodes": completed.size() * 1000}
'''


def export_and_verify(project, godot, template):
    template = str(Path(template).resolve())
    (project / 'export_presets.cfg').write_text('''[preset.0]
name="macOS"
platform="macOS"
runnable=true
export_filter="all_resources"
include_filter=""
exclude_filter="batch/*,*.log,*.json,*.png"
export_path="build/Acceptance.zip"
[preset.0.options]
custom_template/debug=''' + json.dumps(template) + '''
application/bundle_identifier="local.godotrepl.acceptance"
codesign/codesign=1
''')
    (project / 'build').mkdir(exist_ok=True)
    with (project / 'export.log').open('w') as output:
        subprocess.run([godot, '--headless', '--path', str(project), '--export-debug', 'macOS'],
                       stdout=output, stderr=subprocess.STDOUT, check=True, timeout=180)
    # ditto preserves executable mode/symlinks in macOS application bundles.
    subprocess.run(['ditto', '-x', '-k', str(project / 'build/Acceptance.zip'), str(project / 'build/unpacked')], check=True)
    executable = next((project / 'build/unpacked').glob('*.app/Contents/MacOS/*'))
    result = project / 'export-result.json'
    with (project / 'export-run.log').open('w') as output:
        subprocess.run([str(executable), '--headless', '--', '--verify', str(result)],
                       env=dict(os.environ, GODOT_REPL_ENABLE='1'), stdout=output,
                       stderr=subprocess.STDOUT, timeout=30, check=True)
    exported = json.loads(result.read_text())
    assert exported == {'score': 1, 'texture': 64, 'editor_build': False, 'listener_children': 0}, exported
    return exported


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--godot', required=True)
    parser.add_argument('--template', help='Official macos.zip; enables actual macOS debug export and launch')
    args = parser.parse_args()
    scratch = ROOT / '.scratch'
    scratch.mkdir(exist_ok=True)
    (scratch / '.gdignore').touch()
    project = Path(tempfile.mkdtemp(prefix='workflows-', dir=scratch))
    shutil.copytree(ROOT / 'addons', project / 'addons')
    (project / 'project.godot').write_text('''config_version=5
[application]
config/name="REPL Workflow Acceptance"
[autoload]
GodotReplRuntime="*res://addons/godot_repl/runtime.gd"
[editor_plugins]
enabled=PackedStringArray("res://addons/godot_repl/plugin.cfg")
[display]
window/size/viewport_width=900
window/size/viewport_height=600
[rendering]
renderer/rendering_method="gl_compatibility"
textures/vram_compression/import_etc2_astc=true
''')
    (project / 'game.gd').write_text(GAME)
    (project / 'icon.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect width="64" height="64" rx="12" fill="#f49c38"/><circle cx="32" cy="32" r="15" fill="#4b234e"/></svg>')
    with (project / 'import.log').open('w') as output:
        subprocess.run([args.godot, '--headless', '--editor', '--import', '--path', str(project)],
                       stdout=output, stderr=subprocess.STDOUT, check=True, timeout=60)
    launches = []
    checks = []
    def launch(channel):
        result = engine.start(project, args.godot, channel, False)
        launches.append(result['id'])
        return result
    def ev(channel, code, **kwargs):
        response = check(project, channel, code, **kwargs)
        return response['value']
    try:
        editor = launch('editor')
        discovery = ev('editor', '''
var methods = []
for item in ctx.editor.get_method_list():
    methods.append(str(item.name))
var plugins = []
for item in ctx.plugin.get_method_list():
    plugins.append(str(item.name))
var classes = []
for name in ClassDB.get_class_list():
    if "Animation" in name or "Physics" in name:
        classes.append(str(name))
return {"editor": methods, "plugin": plugins, "classes": classes,
    "node_properties": ClassDB.class_get_property_list("Node2D"),
    "button_signals": ClassDB.class_get_signal_list("BaseButton")}
''')
        assert 'get_undo_redo' in discovery['plugin']
        assert 'open_scene_from_path' in discovery['editor']
        assert any(s['name'] == 'pressed' for s in discovery['button_signals'])
        (project / 'discovery.json').write_text(json.dumps(discovery, indent=2))
        assert ev('editor', CREATE, timeout=30) == 'World'
        assert ev('editor', 'return load("res://icon.svg").get_width()') == 64
        checks.append('new project; API/class/property/signal discovery; SVG import, script, scene, animation and persisted input mapping')
        undo = ev('editor', '''
var root = ctx.editor.get_edited_scene_root()
var probe = root.get_node("Probe")
var manager = ctx.plugin.get_undo_redo()
manager.create_action("REPL acceptance position", UndoRedo.MERGE_DISABLE, root)
manager.add_do_property(probe, "position", Vector2(170,220))
manager.add_undo_property(probe, "position", probe.position)
manager.commit_action()
assert(probe.position.x == 170)
var history = manager.get_history_undo_redo(manager.get_object_history_id(root))
assert(history.undo())
assert(probe.position.x == 140)
assert(history.redo())
assert(probe.position.x == 170)
return ctx.editor.save_scene()
''')
        assert undo == 0
        editor = engine.restart(project, editor['id']); launches.append(editor['id'])
        assert ev('editor', 'ctx.editor.open_scene_from_path("res://world.tscn")\nawait ctx.frames(5)\nreturn ctx.editor.get_edited_scene_root().get_node("Probe").position.x') == 170
        checks.append('editor undo/redo, save, managed restart and reopen persisted scene')
        runtime = launch('runtime')
        assert ev('runtime', '''
var event = InputEventKey.new()
event.physical_keycode = KEY_SPACE
event.pressed = true
Input.parse_input_event(event)
await ctx.frames(3)
event = InputEventKey.new()
event.physical_keycode = KEY_SPACE
event.pressed = false
Input.parse_input_event(event)
return ctx.tree.current_scene.score
''') == 1
        assert ev('runtime', '''
var mouse = InputEventMouseMotion.new()
mouse.position = Vector2(400,225)
ctx.tree.root.push_input(mouse, true)
for pressed in [true, false]:
    var event = InputEventMouseButton.new()
    event.button_index = MOUSE_BUTTON_LEFT
    event.position = Vector2(400,225)
    event.pressed = pressed
    ctx.tree.root.push_input(event, true)
    await ctx.frames(2)
return ctx.tree.current_scene.score
''') == 21
        assert ev('runtime', '''
var player = ctx.tree.current_scene.get_node("AnimationPlayer")
player.play("move")
await player.animation_finished
assert(ctx.tree.current_scene.get_node("Probe").position.x == 240)
await ctx.tree.physics_frame
await ctx.tree.physics_frame
var query = PhysicsRayQueryParameters2D.create(Vector2(450,200), Vector2(550,200))
var hit = ctx.tree.current_scene.get_world_2d().direct_space_state.intersect_ray(query)
return hit.collider.name
''') == 'Wall'
        checks.append('key input -> game score; pointer input -> Button signal; animation signal await; physics ray result')
        screenshot = project / 'workflow.png'
        assert ev('runtime', 'await RenderingServer.frame_post_draw\nreturn ctx.tree.root.get_texture().get_image().save_png('+json.dumps(str(screenshot))+')') == 0
        assert screenshot.stat().st_size > 1000
        assert ev('runtime', BATCH.replace('LIMIT', '3'), timeout=60)['verified_nodes'] == 3000
        first = json.loads((project / 'batch/checkpoint.json').read_text())
        ev('runtime', 'ctx.state["unsaved"] = 123')
        # Submit a genuine non-yielding loop. The host cannot service timeout/cancel.
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(repl.execute, project, 'runtime', 'while true:\n    pass', timeout=.1)
            try:
                future.result(timeout=8)
                raise AssertionError('Non-yielding loop unexpectedly returned')
            except repl.ReplError as error:
                assert 'No response' in str(error), error
        runtime = engine.restart(project, runtime['id']); launches.append(runtime['id'])
        assert ev('editor', 'return ctx.editor.get_edited_scene_root().name') == 'World'
        assert ev('runtime', 'return ctx.state.size()') == 0
        assert ev('runtime', BATCH.replace('LIMIT', '10'), timeout=90)['verified_nodes'] == 10000
        final = json.loads((project / 'batch/checkpoint.json').read_text())
        assert final[:3] == first and len(final) == 10
        checks.append('main-thread infinite-loop recovery; fresh session; resume saved checkpoints, verify all 10000 nodes and unchanged completed chunks')
        # Unexpected process loss, using only the known managed test child.
        try:
            repl.execute(project, 'runtime', 'OS.kill(OS.get_process_id())', timeout=2)
        except repl.ReplError:
            pass
        until = time.monotonic() + 8
        while engine.read_status(engine.directory(project, runtime['id']))['state'] not in engine.TERMINAL and time.monotonic() < until:
            time.sleep(.1)
        assert engine.read_status(engine.directory(project, runtime['id']))['state'] == 'exited'
        runtime = engine.restart(project, runtime['id']); launches.append(runtime['id'])
        assert ev('runtime', 'return ctx.tree.current_scene.get_node("Probe").position.x') == 170
        checks.append('unexpected child loss detected; restart and reconnect from saved scene')
        if args.template:
            engine.stop(project, runtime['id'])
            engine.stop(project, editor['id'])
            export_and_verify(project, args.godot, args.template)
            checks.append('actual macOS export template -> app zip -> standalone binary, input/resource behavior and development listener disabled')
        report = {'status': 'PASS', 'evidence': str(project), 'checks': checks,
                  'export_tested': bool(args.template)}
        (scratch / 'workflow-test.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2), flush=True)
    finally:
        for launch_id in reversed(launches):
            engine.stop(project, launch_id)
        print(f'Evidence retained: {project}', flush=True)


if __name__ == '__main__':
    main()
