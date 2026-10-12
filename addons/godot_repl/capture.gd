@tool
extends Logger

var mutex := Mutex.new()
var enabled := false
var messages: Array = []
var errors: Array = []

func begin() -> void:
	mutex.lock()
	messages.clear()
	errors.clear()
	enabled = true
	mutex.unlock()

func snapshot() -> Dictionary:
	mutex.lock()
	var result := {"output": messages.duplicate(true), "errors": errors.duplicate(true)}
	mutex.unlock()
	return result

func stop() -> void:
	mutex.lock()
	enabled = false
	mutex.unlock()

func _log_message(message: String, error: bool) -> void:
	mutex.lock()
	if enabled and messages.size() < 100:
		messages.append({"text": message.left(4096), "stderr": error})
	mutex.unlock()

func _log_error(function: String, file: String, line: int, code: String, rationale: String,
		_editor_notify: bool, error_type: int, _backtraces: Array[ScriptBacktrace]) -> void:
	mutex.lock()
	if enabled and error_type != ERROR_TYPE_WARNING and errors.size() < 100:
		errors.append({"function": function, "file": file, "line": line,
			"message": (rationale if not rationale.is_empty() else code).left(4096)})
	mutex.unlock()
