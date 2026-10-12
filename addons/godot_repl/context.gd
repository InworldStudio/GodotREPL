@tool
extends RefCounted

var state: Dictionary = {}
var tree: SceneTree
var editor: Object
var plugin: Object
var cancelled: bool = false
var request_id: String

func checkpoint() -> bool:
	return not cancelled

func frames(count: int = 1) -> bool:
	for index in range(maxi(count, 0)):
		if cancelled:
			return false
		await tree.process_frame
	return not cancelled

func sleep(seconds: float) -> bool:
	var until := Time.get_ticks_msec() + int(maxf(seconds, 0.0) * 1000.0)
	while Time.get_ticks_msec() < until:
		if cancelled:
			return false
		await tree.process_frame
	return not cancelled
