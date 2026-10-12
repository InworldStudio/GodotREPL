# Godot REPL 0.2 验证记录

日期：2026-10-12。状态：九类验收工作流已有本机实现和运行证据，交付待用户验收。执行、诊断和结果检查由当前 GPT 主会话完成，不声称独立审查或用户验收。总目标和持续接收新任务的标准见 [验收要求](acceptance.md)。

## 环境

- macOS arm64，官方 Godot `4.7.2.stable.official.ed1daf0bf`。
- 引擎归档 SHA-256：`c58a24e31d720be9d62f60cb5627c4e695fb72f21b0cfe1bc9ccaa9a3b3ba63e`，此前与官方 Release API digest 核对一致。
- 本机安装：`/Users/yuexi/Applications/Godot-4.7.2.app`。
- Python 标准库客户端；原生扩展测试使用本机 Xcode Command Line Tools 的 clang。
- macOS 导出模板来自 [官方 4.7.2 Release](https://github.com/godotengine/godot/releases/tag/4.7.2-stable) 的 export_templates.tpz，通过 HTTPS Range 提取 macos.zip，核对 ZIP 成员 CRC。未下载完整 1.28 GB 归档，因此不声称验证了模板总归档 SHA-256。模板与下载缓存留在 `.scratch/templates/`，不进入源码。
- 4.5+ 是基础 API 最低预期；本次只实测 4.7.2。Windows、Linux、.NET 版及其他目标平台未实测。

## 实际通过的测试

| 测试 | 验证内容 | 本机证据 |
| --- | --- | --- |
| `tests/integration.py` | 编辑器与游戏表达式、代码块、完整脚本、跨调用帮助对象、会话隔离、reset；编译/运行/assert 错误；输出与控制字符；await、取消、超时、BUSY；CLI 与交互 shell；无 opt-in 不启用通道；多实例路由 | `.scratch/last-test.json`，目录 `integration-vbjtus9c` |
| `tests/gui_smoke.py` | 0.1 已实测真实编辑器开场景、创建并保存；临时代码不落入工程；运行时修改方块位置和材质，前后截图已查看 | `.scratch/gui-test.json`，目录 `gui-wzci5e3x`；本轮新增图形证据见 workflows |
| `tests/workflows.py` | 空工程创建、ClassDB 与对象接口反射、SVG 导入、脚本与输入映射、场景与动画资源、UndoRedo、保存重开、真实输入和控件信号、动画完成、物理射线、渲染截图、卡死恢复、批次续作、意外退出恢复、独立应用导出与运行 | `.scratch/workflow-test.json`，目录 `workflows-w39gsjj6` |
| `tests/lifecycle.py` | 公开 CLI 的启动/状态/重启/停止；拒绝未知管理 ID；启动超时清理自己的子进程；停止幂等；其他 Godot 进程始终存活 | `.scratch/lifecycle-test.json`，目录 `lifecycle-7f3753i6` |
| `tests/native_extension.py` | 从当前引擎生成 ABI 头文件、编译 C 动态库、通过 REPL 加载、执行原生计算、分别从文件和 REPL 回读结果、卸载 | `.scratch/native-test.json`，目录 `native-y73cxx5u` |
| 桌面操作 | Codex cua_repl 读取 Godot 原生应用菜单、点击 About、核对弹窗文字与截图、关闭弹窗并确认回到项目管理器 | `.scratch/desktop-test.json` 与 `desktop-about.png`；这是外部宿主能力 |

测试代码里的任务逻辑只通过通用 eval、文件、进程和引擎接口完成；插件没有专用的“生成一万个节点”或“触发按钮”等端点。新增的产品入口只有实际 EditorPlugin 引用和通用进程控制器。

## 关键闭环的判定依据

**编辑器**：把节点 X 坐标从 140 改为 170，撤销回到 140，重做回到 170；保存并重启编辑器，重新打开场景后仍为 170。真实图形环境调用 EditorInterface.save_scene，避免用无窗口缩略图渲染替代图形验证。

**输入、动画和物理**：真实按键事件经过 InputMap，使项目脚本分数由 0 变 1；鼠标按下/释放经过 Viewport 与 Button，使分数变 21；等待动画完成后节点 X 为 240；物理射线命中 Wall。工作流截图已查看，图标与按钮实际渲染。

**故障与规模**：先生成三批、共三千节点并保存，记录每批 SHA-256；提交不让出主线程的无限循环，确认 REPL 未响应，再从进程外停止并重启；新会话为空，从磁盘续作到十批、一万个节点，逐个检查编号和坐标，前三批哈希不变。编辑器通道仍可工作。另行让测试游戏进程自杀，控制器检测退出、重启并重新连接到保存的场景。

**构建交付**：使用官方 macOS 模板进行 debug 导出，得到带本地临时签名的应用 ZIP；解包后直接启动其中的独立二进制。结果为 `score=1`、纹理宽度 64、`editor_build=false`、运行时 Autoload 子节点数 0。即使设置 `GODOT_REPL_ENABLE=1`，导出程序也不启动开发通道。未做 release 导出、商店签名或公证。

**原生执行**：C 扩展计算 1 到 1,000,000 的和，文件与 REPL 回读均为 `500000500000`。这证明可编译并运行原生扩展，不证明所有未暴露的引擎私有实现都能直接由 GDExtension 访问。

## 诊断与修复

- 新工程必须完成首次导入后再使用纹理和修改工程设置；首次尝试因资源未导入失败，修正导入顺序后完整工作流通过。
- macOS arm64/universal 导出需要相应纹理导入设置；根据真实导出错误启用 ETC2/ASTC 后，导出与独立运行通过。
- JSON 控制字符转义原来调用 `String.chr(0)`，Godot 自身会报告 NUL 字符错误。改为只转义可存在的 1–31 控制字符；基础协议回归通过。
- 最终工作流、原生扩展与导出运行日志未发现额外引擎错误或泄漏提示。基础回归中的语法、运行和 assert 错误均为预期负例。

## 复现

在 GodotREPL 目录运行，按本机情况替换引擎和模板路径：

```sh
python3 tests/integration.py --godot /absolute/path/to/Godot
python3 tests/gui_smoke.py --godot /absolute/path/to/Godot
python3 tests/lifecycle.py --godot /absolute/path/to/Godot
python3 tests/workflows.py --godot /absolute/path/to/Godot --template /absolute/path/to/macos.zip
python3 tests/native_extension.py --godot /absolute/path/to/Godot
```

`workflows.py` 省略模板参数时跳过导出，不能拿该报告证明导出通过。桌面验证通过当前宿主的 cua_repl 单独执行，不冒充 Python 自动化测试。每次运行生成独立临时工程并保留日志，仅结束该次启动的进程。

## 实际边界

- 进程内执行仍是 GDScript；无 C# 交互编译或 MCP 适配。AI 通过 CLI 调用，文件、编译、原生扩展和桌面工具提供补充路径。
- 同步死循环恢复需要终止进程；未保存改动与内存会话丢失，不等于回滚。控制器只管理自己的直接子进程，控制器本身失联会明确报错。
- 日志捕获按执行时间窗口归属，不是精确异常隔离。返回值有大小限制，完整数据应保存为文件或分页读取。
- 撤销需要调用代码主动组织 action，Node owner 与保存仍需显式处理。
- 桌面工具未内置，宿主需要提供相应访问能力。此次原生菜单样本不证明所有窗口路由与 UI 工作流已覆盖。
- 没有运行修改引擎源码的构建，也没有证明所有未来任务必然可完成。新任务暴露阻断时按验收要求继续补齐。

## 交付与归属

独立本地仓库 `GodotREPL`，分支 `codex/godot-repl-v1`，空仓库无基线提交、无远端。源码、文档与证据保留在本任务目录；验证后仅暂存，未 commit、push、合并或发布。目录含唯一成果，不能回收。

山海消费侧由 [Issue #11](https://github.com/InworldStudio/Shanhai/issues/11) 跟踪。没有修改山海或 AGW 的原型、源码与工作区。
