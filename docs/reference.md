# REPL 执行参考

[快速接入](../README.md) · [AI 操作指南](agent-guide.md)

## 代码与上下文

`-e` 是表达式；`-c` 和 `-f` 是 `func run(ctx)` 的函数体，可使用变量、循环、条件、lambda、引擎 API、项目方法和 `await`。用 `return` 返回值；没有 return 时成功返回 null。`-f -` 从标准输入读取。

```gdscript
ctx.state["count"] = ctx.state.get("count", 0) + 1
var player = ctx.tree.root.get_node("Main/Player")
ctx.state["player"] = player
if not await ctx.frames(2):
    return "cancelled"
return ctx.state["count"]
```

`ctx` 提供：

| 属性／方法 | 含义 |
| --- | --- |
| `state` | 当前命名会话的 Dictionary，可保存对象引用和 Callable |
| `tree` | 当前通道进程的 SceneTree |
| `editor` | 编辑器通道的 EditorInterface，游戏通道为 null |
| `plugin` | 编辑器通道的实际 EditorPlugin，可取得 UndoRedo 等服务；游戏通道为 null |
| `request_id` | 当前请求 ID |
| `cancelled` / `checkpoint()` | 协作式取消标记／是否可以继续 |
| `await frames(n)` | 等待 n 帧；取消后返回 false |
| `await sleep(seconds)` | 等待指定秒数；取消后返回 false |

**局部 `var` 不会自动保留到下一次调用。** 跨调用状态显式放进 `ctx.state`，避免历史代码重放。函数体不接受顶层 `class_name` / `extends` / 方法声明。`-s 文件` 提交完整 `@tool`、`extends RefCounted` 脚本，入口为接收一个无类型标注参数的 `run(ctx)`，构造函数不得要求参数，可以定义其他方法；用 `ctx.state["helper"] = self` 保留帮助实例，后续再调用其方法。也可以加载项目脚本。保存 Callable 时同时保留其宿主对象，避免宿主释放。

会话由工程、进程、通道和 `--session` 共同隔离。最多 32 个命名会话；`reset` 删除当前会话的 state。状态只存在内存中，进程重启后失效；Node 释放后保存的引用也会失效，调用者使用 `is_instance_valid()` 检查。

## 结果、错误与取消

返回 JSON：`status`、`value`、`output`、`errors`、请求及实例身份。退出码：0 成功、1 引擎返回失败、2 客户端失败。`print()` 与引擎错误通过 Logger 捕获；生成脚本的第 4 行对应代码块第 1 行。

结果只序列化摘要：Vector3 等 Variant 返回类型与文字，Object 返回类型和 instance_id，实际对象留在 state；数组／字典最多返回前 1000 项，嵌套深度最多 12，总处理项最多 4096。输出最多 100 条，每条 4096 字符。不要把大批资源或完整世界数据作为一次返回值。

同一通道同一时刻只执行一个请求，其余返回 `BUSY`，避免交错的执行与日志归属。捕获的是该进程执行窗口内的日志，因此游戏其他逻辑在此期间报错也会使请求保守地返回 `RUNTIME_ERROR`；这不是精确的异常栈隔离。

`--timeout` 默认 10 秒，范围 0.001–300 秒。超时或客户端 Ctrl-C 会提出协作式取消；在等待点用 `ctx.frames/sleep` 并检查返回值。**超时不会回滚改动，也不能强行中断同步死循环。** 任意 `await` 超时后仍可能恢复并产生副作用，通道保持 BUSY，直到原代码结束。同步死循环会堵住 Godot 主线程，此时 REPL 报告未响应；AI 可通过[进程外控制器](agent-guide.md#超时卡死与恢复)恢复其管理的进程。

场景编辑直接调用引擎 API，并不自动获得 Undo。需要可撤销编辑时，通过编辑器的 UndoRedo 管理器组织操作；新增节点需要设置 owner 才能随场景保存。保存由代码显式调用 `ctx.editor.save_scene()`。

## 交互终端

`python3 tools/repl.py --project /path/to/game --channel runtime shell` 启动交互 shell。默认求值表达式；`:paste` 开始多行代码，`:end` 执行，`:reset` 清理当前会话，`:quit` 退出。通用参数放在子命令之前。

## 本地执行权限

插件是开发者级任意代码执行工具，不是代码沙箱。只执行你信任的请求。没有网络端口，IPC 位于目标工程的 `.godot/godot_repl/<channel>/<instance>/`；Unix 上实例目录设为 0700、结果文件 0600，Windows 依赖用户目录 ACL。请求使用随机 ID、原子写入、大小限制和 JSON 校验。旧进程的请求不会被新实例重放。

正常退出删除实例登记；客户端通过进程存活检查忽略崩溃后的旧登记。未被客户端领取的结果文件可留在 `.godot`，这是缓存，不进入版本管理。
