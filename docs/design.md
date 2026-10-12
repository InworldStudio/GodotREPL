# 设计与现成方案评估

状态：0.2 本地交付待验收。范围：独立 Godot REPL。来源：2026-10-12 用户要求；山海工具验证跟踪 [Issue #11](https://github.com/InworldStudio/Shanhai/issues/11)。

0.1 是基础执行层里程碑；项目总目标与后续能力缺口以 [验收要求](acceptance.md) 为准，不以本页的初版范围限制最终能力。

## 为什么保留自己的薄执行层

- Godot 官方调试器已有断点上下文表达式 REPL，但它不等价于供外部 Agent 调用的完整语句与异步执行通道。
- [GDScript-REPL](https://github.com/matheusfillipe/GDScript-REPL) 明确标注实验性质、多行输入限制和脚本缓冲处理，主要提供独立解释环境。
- [GDScript Input Field](https://github.com/tkmfujise/gdscript_input_field) 提供编辑器面板内交互。未实测其外部 Agent 双通道能力。
- [Godot MCP Toolkit](https://npgamedev.github.io/godot-mcp-server/tool-reference/#execute_code) 的 execute_code 文档明确为 expression-only，不接受 var/return/if/for/赋值。
- [satelliteoflove/godot-mcp](https://github.com/satelliteoflove/godot-mcp/blob/main/docs/tools/exec.md) 提供运行时 GDScript 执行，但文档写明不支持 await。

这些是文档核对，不是对整个生态不存在合适实现的证明，也不是对上述项目的实机评测。基于当前可验证差异，本工程优先实现我们需要的窄接口；没有复制第三方实现。

## 决策

GDScript.new → source_code → reload → 实例 run(ctx)。代码块只编译一次、只执行一次，局部变量不模拟跨调用词法绑定，跨调用对象保存在显式 state。

同一进程通道串行执行，运行时与编辑器各有 Host；文件 IPC 不引入监听端口。每次启动创建随机实例目录，多开时由客户端明确选实例。

Logger 捕获输出与错误，不能只依据返回 null 判断成功。跨帧执行依靠 GDScript await；超时与取消不声称能抢占主线程，超时任务未结束时阻止新任务。

初版重点是可组合的代码执行基础，暂不提供 MCP 适配、可视化控制台、C# 交互编译、自动撤销或任意代码硬中断。这些都不影响 CLI/Agent 使用基础通道。

## 0.2：补齐完整工作流所需的入口

Context 暴露实际 EditorPlugin，供代码自行发现并调用编辑器服务，包含 UndoRedo；不增加专用的创建场景、按钮点击或批量生成端点。[官方 UndoRedo 文档](https://docs.godotengine.org/en/stable/classes/class_editorundoredomanager.html) 说明了从插件获取管理器及场景历史的用法。

进程外控制器为每次启动保留独立 supervisor 和直接子进程句柄，使用私有目录接收停止请求。停止不依赖 Godot 主线程响应；重启创建新实例并读取已保存工程。控制器不承诺内存回滚，也不通过旧 PID 接管用户的其他进程。

文件、构建与原生编译使用外部工具；界面操作使用 AI 宿主的桌面能力。C GDExtension 样本验证原生执行路径，完整工作流样本验证导出程序行为。[官方命令行文档](https://docs.godotengine.org/en/stable/tutorials/editor/command_line_tutorial.html) 是导入、API 导出和应用导出的参考入口。
