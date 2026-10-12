@tool
extends Node

const Context = preload("res://addons/godot_repl/context.gd")
const Capture = preload("res://addons/godot_repl/capture.gd")
const MAX_BYTES := 262144
const MAX_SESSIONS := 32
var channel := "runtime"
var editor: Object
var plugin: Object
var directory := ""
var instance_id := ""
var sessions: Dictionary = {}
var active: Dictionary = {}
var capture
var last_heartbeat := 0
var stopped := false

func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	instance_id = str(OS.get_process_id()) + "-" + Crypto.new().generate_random_bytes(8).hex_encode()
	directory = ProjectSettings.globalize_path("res://.godot/godot_repl/" + channel + "/" + instance_id)
	for part in [directory, directory + "/requests", directory + "/responses"]:
		DirAccess.make_dir_recursive_absolute(part)
		FileAccess.set_unix_permissions(part, 448)
	capture = Capture.new()
	OS.add_logger(capture)
	_heartbeat()

func _heartbeat() -> void:
	last_heartbeat = Time.get_ticks_msec()
	_write(directory + "/instance.json", {"protocol": 1, "pid": OS.get_process_id(),
		"instance": instance_id, "channel": channel, "project": ProjectSettings.globalize_path("res://"),
		"version": Engine.get_version_info().string, "heartbeat": Time.get_unix_time_from_system(),
		"managed_id": OS.get_environment("GODOT_REPL_MANAGED_ID"),
		"busy": not active.is_empty()})

func _process(_delta: float) -> void:
	if stopped:
		return
	if Time.get_ticks_msec() - last_heartbeat > 1000:
		_heartbeat()
	if not active.is_empty() and not active.responded:
		var cancel_path: String = directory + "/requests/" + active.id + ".cancel"
		if FileAccess.file_exists(cancel_path):
			DirAccess.remove_absolute(cancel_path)
			_interrupt("CANCELLED")
		elif Time.get_ticks_msec() >= active.deadline:
			_interrupt("TIMEOUT")
	var files := DirAccess.get_files_at(directory + "/requests")
	files.sort()
	var processed := 0
	for filename in files:
		if not filename.ends_with(".json"):
			continue
		var id := filename.trim_suffix(".json")
		if not _valid_id(id):
			continue
		_accept(id)
		processed += 1
		if processed >= 8:
			break

func _valid_id(id: String) -> bool:
	return id.length() == 32 and id.is_valid_hex_number(false)

func _accept(id: String) -> void:
	var path := directory + "/requests/" + id + ".json"
	var file := FileAccess.open(path, FileAccess.READ)
	if file == null:
		return
	var size := file.get_length()
	var raw := file.get_as_text() if size <= MAX_BYTES else ""
	file.close()
	DirAccess.remove_absolute(path)
	var parser := JSON.new()
	if size > MAX_BYTES or parser.parse(raw) != OK or not parser.data is Dictionary:
		_reply(id, "INVALID_REQUEST", null, "Expected a JSON object, at most 256 KiB")
		return
	var cancel_path := directory + "/requests/" + id + ".cancel"
	if FileAccess.file_exists(cancel_path):
		DirAccess.remove_absolute(cancel_path)
		_reply(id, "CANCELLED", null, "Cancelled before execution")
		return
	var request: Dictionary = parser.data
	if request.get("id") != id or request.get("protocol") != 1:
		_reply(id, "INVALID_REQUEST", null, "Protocol or request ID mismatch")
		return
	var expires = request.get("expires_at", 0)
	if not (expires is float or expires is int) or expires <= Time.get_unix_time_from_system():
		_reply(id, "EXPIRED", null, "Request expired before execution")
		return
	if not active.is_empty():
		_reply(id, "BUSY", null, "Previous code is still running; timed-out code must finish before reuse")
		return
	var session = request.get("session", "default")
	if not session is String or session.is_empty() or session.length() > 128:
		_reply(id, "INVALID_REQUEST", null, "Invalid session name")
		return
	var operation = request.get("op", "eval")
	if operation == "reset":
		sessions.erase(session)
		_reply(id, "OK", true)
		return
	if operation != "eval":
		_reply(id, "INVALID_REQUEST", null, "Unknown operation")
		return
	var code = request.get("code")
	var timeout = request.get("timeout_ms", 10000)
	var mode = request.get("mode", "block")
	if not code is String or code.strip_edges().is_empty() or not (timeout is int or timeout is float):
		_reply(id, "INVALID_REQUEST", null, "Code and numeric timeout required")
		return
	if timeout < 1 or timeout > 300000 or mode not in ["block", "expr", "script"]:
		_reply(id, "INVALID_REQUEST", null, "Invalid timeout or mode")
		return
	if not sessions.has(session):
		if sessions.size() >= MAX_SESSIONS:
			_reply(id, "SESSION_LIMIT", null, "Reset an unused session first")
			return
		sessions[session] = {}
	var ctx = Context.new()
	ctx.state = sessions[session]
	ctx.tree = get_tree()
	ctx.editor = editor
	ctx.plugin = plugin
	ctx.request_id = id
	active = {"id": id, "ctx": ctx, "deadline": Time.get_ticks_msec() + int(timeout), "responded": false}
	capture.begin()
	_execute(code, mode, ctx)

func _execute(code: String, mode: String, ctx) -> void:
	var source := "@tool\nextends RefCounted\nfunc run(ctx):\n"
	var body := "return (" + code + ")" if mode == "expr" else code
	var lines := body.replace("\r\n", "\n").split("\n")
	var indent := "    "
	for line in lines:
		if line.begins_with("\t"):
			indent = "\t"
			break
	for line in lines:
		source += indent + line + "\n"
	if mode == "script":
		source = code
	var script := GDScript.new()
	script.resource_name = "REPL_" + ctx.request_id
	script.source_code = source
	var error := script.reload()
	if error != OK:
		_finish("COMPILE_ERROR", null, "GDScript compilation failed; snippet lines start at generated line 4")
		return
	if script.get_instance_base_type() != "RefCounted":
		_finish("INVALID_SCRIPT", null, "Script must extend RefCounted")
		return
	if not script.can_instantiate() or (channel == "editor" and not script.is_tool()):
		_finish("INVALID_SCRIPT", null, "Script must be instantiable and use @tool in editor")
		return
	var has_entry := false
	for method in script.get_script_method_list():
		if method.name == "_init" and method.args.size() > method.default_args.size():
			_finish("INVALID_SCRIPT", null, "Constructor must not require arguments")
			return
		if method.name == "run":
			has_entry = method.args.size() == 1 and method.args[0].type == TYPE_NIL
	if not has_entry:
		_finish("INVALID_SCRIPT", null, "Script must implement run(ctx) with one untyped argument")
		return
	var evaluator = script.new()
	if evaluator == null or not evaluator.has_method("run"):
		_finish("INVALID_SCRIPT", null, "Script must implement run(ctx)")
		return
	var result = await evaluator.run(ctx)
	var logs: Dictionary = capture.snapshot()
	if not logs.errors.is_empty():
		_finish("RUNTIME_ERROR", null, "Engine errors occurred during execution; see errors")
	else:
		_finish("OK", result)

func _interrupt(status: String) -> void:
	active.ctx.cancelled = true
	active.responded = true
	_reply(active.id, status, null, "Cooperative cancellation requested; no rollback or hard preemption", capture.snapshot())

func _finish(status: String, value = null, message := "") -> void:
	if active.is_empty():
		return
	if not active.responded:
		_reply(active.id, status, value, message, capture.snapshot())
	capture.stop()
	active.clear()

func _reply(id: String, status: String, value = null, message := "", logs: Dictionary = {}) -> void:
	_write(directory + "/responses/" + id + ".json", {"protocol": 1, "id": id,
		"instance": instance_id, "channel": channel, "status": status,
		"value": _encode(value, 0, [4096]), "message": message,
		"output": logs.get("output", []), "errors": logs.get("errors", [])})

func _encode(value, depth: int = 0, budget: Array = [4096]):
	budget[0] -= 1
	if budget[0] < 0:
		return {"type": "truncated", "reason": "item budget"}
	if depth > 12:
		return {"type": "truncated", "reason": "depth limit"}
	if value == null:
		return null
	match typeof(value):
		TYPE_BOOL, TYPE_INT, TYPE_STRING:
			return value
		TYPE_STRING_NAME:
			return str(value)
		TYPE_FLOAT:
			return value if is_finite(value) else {"type": "float", "text": str(value)}
		TYPE_ARRAY:
			var result := []
			for item in value.slice(0, 1000):
				if budget[0] <= 0:
					result.append({"type": "truncated", "reason": "item budget"})
					break
				result.append(_encode(item, depth + 1, budget))
			return result
		TYPE_DICTIONARY:
			var result := {}
			for key in value.keys().slice(0, 1000):
				if budget[0] <= 0:
					break
				result[str(key)] = _encode(value[key], depth + 1, budget)
			return result
		TYPE_OBJECT:
			if not is_instance_valid(value):
				return {"type": "freed_object"}
			return {"type": value.get_class(), "instance_id": str(value.get_instance_id())}
		_:
			return {"type": type_string(typeof(value)), "text": str(value)}

func _write(path: String, data: Dictionary) -> void:
	var temp := path + ".tmp"
	var file := FileAccess.open(temp, FileAccess.WRITE)
	if file == null:
		return
	var encoded := JSON.stringify(data)
	# Logger output may contain ANSI control bytes, which must be escaped in JSON.
	# Godot Strings cannot contain NUL; String.chr(0) itself logs an engine error.
	for character in range(1, 32):
		encoded = encoded.replace(String.chr(character), "\\u%04x" % character)
	file.store_string(encoded)
	file.close()
	FileAccess.set_unix_permissions(temp, 384)
	DirAccess.rename_absolute(temp, path)

func shutdown() -> void:
	if stopped:
		return
	stopped = true
	if not active.is_empty() and not active.responded:
		_interrupt("SHUTDOWN")
	if capture != null:
		OS.remove_logger(capture)
	DirAccess.remove_absolute(directory + "/instance.json")

func _exit_tree() -> void:
	shutdown()
