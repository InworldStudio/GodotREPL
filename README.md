# Godot REPL

让 AI 在 Godot 编辑器和运行中的游戏里执行 GDScript，查询、修改场景与资源，并读取执行结果。提供命令行客户端和进程控制器，使用本地文件 IPC。

## 快速接入

需要 Godot 4.5+、Python 3.9+，以及 AI 的文件读写和命令行能力。已实测 macOS / Godot 4.7.2。以下命令在同一终端执行，先替换三个绝对路径：

```sh
REPL_ROOT="/absolute/path/to/GodotREPL"
GAME_ROOT="/absolute/path/to/game"
GODOT_BIN="/absolute/path/to/Godot"
```

### 1. 安装插件

确认目标目录包含 `project.godot`。若已存在 `addons/godot_repl`，先检查版本与本地改动，再决定更新；首次安装执行：

```sh
mkdir -p "$GAME_ROOT/addons"
cp -R "$REPL_ROOT/addons/godot_repl" "$GAME_ROOT/addons/"
```

客户端保留在 `REPL_ROOT`，通过 `--project` 指向目标工程。

### 2. 合并工程配置

将以下配置合并到目标 `project.godot`。保留已有配置节、Autoload 和插件；已有 `enabled` 数组只追加插件路径，不覆盖整个数组或重复创建配置节。

```ini
[autoload]
GodotReplRuntime="*res://addons/godot_repl/runtime.gd"

[editor_plugins]
enabled=PackedStringArray("res://addons/godot_repl/plugin.cfg")
```

### 3. 导入并启动

先完成资源导入，再启动编辑器和游戏。运行时通道需要工程已配置主场景。

```sh
"$GODOT_BIN" --headless --editor --import --path "$GAME_ROOT"
python3 "$REPL_ROOT/tools/engine.py" --project "$GAME_ROOT" start --godot "$GODOT_BIN" --channel editor
python3 "$REPL_ROOT/tools/engine.py" --project "$GAME_ROOT" start --godot "$GODOT_BIN" --channel runtime
```

每次启动返回 `id`（管理 ID）、`instance`（REPL 实例 ID）和日志路径。控制器自动启用运行时通道。自行启动 Godot 时需设置环境变量 `GODOT_REPL_ENABLE=1`；导出程序不会开启该通道。

### 4. 验证接入

```sh
python3 "$REPL_ROOT/tools/repl.py" --project "$GAME_ROOT" --channel editor eval -e 'ctx.plugin.get_undo_redo() != null'
python3 "$REPL_ROOT/tools/repl.py" --project "$GAME_ROOT" --channel runtime eval -e 'ctx.tree.current_scene.name'
```

两次返回的 `status` 应为 `OK`，`value` 分别为 `true` 和主场景节点名称。失败时读取 `errors` 与启动日志。同一通道多开时，先用 `list` 查询，再在 `eval` 前加 `--instance INSTANCE_ID` 明确选择。

## 执行任务

表达式用 `eval -e`，多行代码用 `eval -f FILE`，标准输入用 `eval -f -`：

```sh
python3 "$REPL_ROOT/tools/repl.py" --project "$GAME_ROOT" --channel runtime eval -f - <<'GDSCRIPT'
ctx.state["count"] = ctx.state.get("count", 0) + 1
if not await ctx.frames(2):
    return "cancelled"
return ctx.state["count"]
GDSCRIPT
```

`ctx.tree` 访问场景树；编辑器通道还提供 `ctx.editor` 和 `ctx.plugin`。跨调用状态放入 `ctx.state`，用 `return` 返回结果。新操作通过对象反射、ClassDB 和引擎 API 组合实现。

## 停止与恢复

```sh
python3 "$REPL_ROOT/tools/engine.py" --project "$GAME_ROOT" restart MANAGED_ID
python3 "$REPL_ROOT/tools/engine.py" --project "$GAME_ROOT" stop MANAGED_ID
```

使用启动返回的管理 ID。重启后使用新的实例 ID；未保存改动和内存会话会丢失。执行超时不会回滚或强制中断同步死循环，进程控制器可从外部恢复自己启动的进程。

## 进一步使用

- [执行参考](docs/reference.md)：完整脚本、上下文、会话、返回格式、错误与限制。
- [AI 操作指南](docs/agent-guide.md)：接口发现、场景保存、输入与截图、批处理、原生扩展和故障恢复。
