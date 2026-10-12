# AI 操作指南

本工程提供通用执行通道，不包含模型。AI 需要能读写工程文件、调用命令行；界面专属任务还需要桌面控制工具。这里的命令从 GodotREPL 工程目录运行，`/path/to/game`、`/path/to/Godot` 替换为实际路径。

## 连接目标工程

首次安装、配置合并、导入、启动和连通性验证按 [README 快速接入](../README.md#快速接入) 执行。启动成功表示通道已登记，不代表游戏异步加载已经完成。

管理 ID 用于启停；REPL 实例 ID 用于执行代码。多实例必须明确选择。已由用户启动的编辑器也可连接，但不受进程控制器管理。

```sh
python3 tools/repl.py --project /path/to/game --channel editor list
python3 tools/repl.py --project /path/to/game --channel editor --instance INSTANCE_ID eval -e 'ctx.editor.get_edited_scene_root()'
```

代码格式、上下文、输出和错误码见 [执行参考](reference.md)。

## 遇到新操作

先发现能力，再组织代码。编辑器通道提供 `ctx.editor` 和 `ctx.plugin`；运行时提供真实的 `ctx.tree`。GDScript 可直接访问 ClassDB、引擎单例、项目脚本及对象反射，不需要新增工具命令。

```gdscript
return ctx.editor.get_method_list().filter(func(m): return "scene" in str(m.name))
```

```gdscript
var node = ctx.tree.current_scene
return {
    "class": node.get_class(),
    "methods": node.get_method_list(),
    "properties": node.get_property_list(),
    "signals": node.get_signal_list()
}
```

结果有大小限制。先筛选或分页；完整数据可由代码写入工程外的指定文件，再通过文件工具读取。不能把被截断的结果误认为全部接口。

如需匹配当前引擎的完整 API 与说明，可在工程外的临时目录运行：

```sh
/path/to/Godot --headless --dump-extension-api-with-docs
/path/to/Godot --headless --dump-gdextension-interface
```

不确定接口语义时查当前版本官方文档或引擎源码；反射能证明入口存在，不能证明使用方式正确。

## 执行、观察和保存

把多步代码写入临时文件，通过 `eval -f` 提交；需要帮助方法或回调时，用 `eval -s` 的完整脚本或工程内脚本。跨调用对象保存在 `ctx.state`；首次取得对象后，在后续调用中检查其有效性。

场景保存前设置节点 owner；需要撤销历史时通过 `ctx.plugin.get_undo_redo()` 组织 action。显式验证撤销、重做后的属性。资源导入后等待文件系统扫描完成，再检查实际加载结果。修改工程脚本后验证编译和运行，不以写入文件成功代替运行成功。

运行时可使用 `Input.parse_input_event` 或 Viewport 的输入入口，并验证实际项目逻辑或控件信号；不能把直接改分数当作键盘测试。动画、物理、异步加载等工作等待相应帧或信号。截图需在真实渲染进程中等待 `RenderingServer.frame_post_draw` 后获取。

每次操作至少保留一种独立结果：回读的属性、保存并重载的文件、日志、截图、运行产物或完整断言。请求返回 OK 仅说明该执行窗口内没有捕获到引擎错误。

## 超时、卡死与恢复

`ctx.frames()`、`ctx.sleep()` 支持协作取消，但调用代码必须检查返回值。同步死循环会堵住主线程，进程内 REPL 无法中断它。

```sh
python3 tools/engine.py --project /path/to/game status
python3 tools/engine.py --project /path/to/game stop MANAGED_ID
python3 tools/engine.py --project /path/to/game restart MANAGED_ID
```

控制器在进程外运行，保留自己启动的直接子进程句柄。停止先请求进程终止，三秒未结束再强制结束；不依据从工程文件读出的 PID 杀进程。`restart` 创建新管理 ID 和新实例，不重放旧请求。若控制器本身失联，停止命令会明确失败，不猜测 PID。`status` 中的 heartbeat 是控制器更新时间，不代表 Godot 主线程响应正常。

重启从已保存工程加载，未保存场景修改和内存会话会丢失。先保存工作，再执行预期会阻塞的实验；重要批任务把完成批次原子保存并记录校验值。恢复后验证磁盘结果，从已完成范围继续。控制器只管理直接启动的进程，不声称接管编辑器启动的所有后代进程。

## 脚本接口之外

工程文件、构建配置、编译器与导出命令通过文件和进程工具操作。只有界面入口的工作使用桌面控制：识别目标窗口、读取当前界面、操作、重新读取并核对结果。桌面控制由 AI 宿主提供，未打包进 Python 客户端。

需要原生代码时，使用当前引擎导出的 GDExtension ABI 编译扩展，再通过 `GDExtensionManager` 加载并验证。可复现样本见 [原生扩展测试](../tests/native_extension.py)。扩展仍受 Godot 暴露的原生接口约束；修改引擎内部实现需要独立的引擎源码构建，不能把扩展样本说成已经覆盖所有私有实现。

导出需要匹配版本的模板及目标平台工具链。导出后启动独立应用并验证资源、输入与开发通道关闭；示例见 [完整工作流测试](../tests/workflows.py)。本地临时签名用于运行测试，不代表应用商店签名、公证或发布完成。

## 大规模工作与新任务验收

将大工作拆成可检查和保存的批次，在批次边界等待帧并检查取消标记。持久结果作为恢复依据，不能只依赖 ctx.state。每批记录完成范围及产物校验值，恢复时回读校验，再继续未完成批次。

接到未预设任务时，按上述路径找到接口、编写临时代码、执行并验证。若需要新专用命令才能继续，先判断是否通用通道缺能力；发现缺口就补通用入口及测试，不能把一个固定操作清单当作最终能力范围。
