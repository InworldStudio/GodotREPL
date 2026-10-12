extends Node

func _ready() -> void:
	# No listener in exports, including debug exports. Opt in per process.
	if not OS.has_feature("editor") or OS.get_environment("GODOT_REPL_ENABLE") != "1":
		return
	var host = load("res://addons/godot_repl/host.gd").new()
	host.channel = "runtime"
	add_child(host)
