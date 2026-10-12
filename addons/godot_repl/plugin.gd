@tool
extends EditorPlugin

const Host = preload("res://addons/godot_repl/host.gd")
var host

func _enter_tree() -> void:
	host = Host.new()
	host.channel = "editor"
	host.editor = EditorInterface
	host.plugin = self
	add_child(host)

func _exit_tree() -> void:
	if is_instance_valid(host):
		host.shutdown()
		host.queue_free()
