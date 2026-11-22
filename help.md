# Onyx — Help

**English** · 中文版见下半部分 ↓

Onyx is a terminal emulator, shell and security sandbox written in Python. Syntax highlighting,
ghost completion, security interception and AI builtin commands all live in the input layer, while
the shell that actually runs your commands is a real `bash`/`zsh` attached through a PTY.

## Contents

- [Quick Start](#quick-start)
- [Architecture](#architecture)
- [Builtin Commands](#builtin-commands)
- [Sub-commands](#sub-commands)
- [Language](#language)
- [MCP](#mcp-model-context-protocol)
- [Task System](#task-system)
- [TBS vs OS Mode](#tbs-vs-os-mode)
- [Security Modes](#security-modes)
- [Path-level Permissions](#path-level-permissions)
- [Dangerous Command Blacklist](#dangerous-command-blacklist)
- [AI Features](#ai-features)
- [Proactive Perception (Senses)](#proactive-perception-senses)
- [Ghost Completion](#ghost-completion)
- [Project Structure](#project-structure)

## Quick Start

Install Onyx with the provided script, then type `onyx` in any terminal.

1. **First launch** — Onyx asks you to set the password for **advanced (`adv`) mode**.
2. **Sandbox** — it then creates a *virtual root*: a directory that mimics `/`, so your real
   filesystem stays out of reach. Turn it off with `manage set sandbox false`.
3. **Everyday use** — for ordinary work Onyx behaves like a normal shell.
4. **Safety net** — commands that could damage the system are intercepted and must be confirmed
   (password or verification code) before they run.
5. **AI** — type `ai` to enter AI mode. You configure the backend on first use. The AI can edit
   code, run commands and carry out complex multi-step tasks for you.

## Architecture

```
keyboard input → Onyx (input · parse · security · AI) → PTY → bash/zsh → kernel
```

Onyx never replaces your shell: it owns the input layer and lets a real `bash`/`zsh` do the work.

## Builtin Commands

| Command | Description |
|------|------|
| `exit` | Exit Onyx |
| `refresh` | Refresh the tool index |
| `export <VAR>=<value>` | Set an environment variable |
| `activite` | Activate / switch the security mode (`low` / `mid` / `adv`) |
| `manage <subcommand>` | Manage configuration (get / set config items) |
| `switch-prompt <style>` | Switch the prompt style (`kali` / `ubuntu` / `zsh` / `onyx` / `termux` / `def` / `skali`) |
| `ai <prompt>` | AI assistant (requires platform API key) |
| `ai -key <API Key>` | Set the API key for the current platform (stored in `~/.config/onyx/ai/key.json`) |
| `ai config <sub>` | Personal config without the menu: `show` / `get` / `set` / `test` / `doctor` / `backup` / `restore` / `path` |
| `ai -repl` / `ai -tui` | Force the interactive mode (REPL / full-screen TUI); bare `ai` uses the default |
| `set-adv-pwd` | Set the advanced-mode password |
| `help` | Show this help |
| `mktool -n <name> -l <lang>` | Create a new toolkit plugin and generate its config / permission files. Languages: python / c / cpp / bash. Tool path: `tools/plugin/<name>`. Example: `mktool -n port_scanner -l python` |
| `sado <cmd>` | Run a command with elevated privileges (sudo-like, through Onyx's permission system) |
| `nanosado` | Lightweight privilege escalation |
| `history` | Show the command history |

> `activite` is a historical spelling kept for compatibility — it *activates* the security mode.

## Sub-commands

### `manage` — configuration

| Sub-command | Description |
|------|------|
| `manage set <key> <value>` | Set a config item |
| `manage get <key>` | Read a config item |
| `manage set language english` | Switch the UI to English |
| `manage set language chinese` | Switch the UI to Chinese |
| `manage set sandbox false` | Disable the virtual-root sandbox |
| `manage set debug-times true` | Show per-command execution time |
| `manage set debug-parsecmd true` | Enable command-parsing debug output |
| `manage set clean-log-time <days>` | Set the log auto-clean interval |
| `manage shell <name>` | Set the shell used by **both** the input layer and the underlying PTY (bash/zsh/fish/sh/pwsh/cmd, or a path) |
| `manage shell` | Show the current shell |

### `activite` — security mode

| Flag | Description |
|------|------|
| `activite -m low` | Strict mode — whitelisted commands only |
| `activite -m mid` | Moderate mode — relaxed interception ceiling |
| `activite -m adv` | Advanced mode — confirmation dialog + argon2id password |

### `switch-prompt` — prompt styles

| Style | Description |
|------|------|
| `switch-prompt kali` | Kali Linux style |
| `switch-prompt ubuntu` | Ubuntu style |
| `switch-prompt zsh` | Z shell style |
| `switch-prompt onyx` | Onyx default style |
| `switch-prompt termux` | Termux style |
| `switch-prompt def` | Default minimal style |
| `switch-prompt skali` | Simplified Kali style |

## Language

The Onyx UI is bilingual. You can switch at any time:

| Command | Effect |
|------|------|
| `manage set language english` | Switch the UI to English |
| `manage set language chinese` | Switch the UI to Chinese |

- The choice is persisted to `~/.config/onyx/language` and survives restarts.
- It applies to the prompt, help text, error messages and the AI module.
- Defaults live in `etc/config.json` under `display_info.language` (default: `Chinese`;
  supported: `Chinese`, `English`).

## MCP (Model Context Protocol)

Onyx supports MCP for AI tool integration:

- `etc/mcp/mcp.json` — MCP server configuration
- `bin/ai_lib/mcp_client.py` — MCP client implementation
- `bin/ai_lib/mcp_registry.py` — tool registry for MCP tools
- `bin/ai_lib/mcp_transport.py` — transport layer (SSE / stdio)

MCP lets the AI call external tools through a standard protocol, extending what it can do.

## Task System

Onyx ships with a built-in task system (`lib/task_system/`):

- **Task Registry** — register and manage tasks
- **Cron Registry** — scheduled task execution
- **Team Registry** — team-based task coordination
- **Task Packet** — structured task definitions

## TBS vs OS Mode

| Mode | Behaviour |
|------|------|
| **TBS** | Virtual sandbox — every operation stays inside the virtual root; the real system is untouched. |
| **OS** | Full system access — the virtual root *is* the system root; full read/write. |

Onyx detects the mode automatically: when `ROOT_DIR` equals the system root (`/` or `C:\`), OS mode
is active.

## Security Modes

| Mode | Description |
|------|------|
| `low` | Strict interception — whitelisted commands only |
| `mid` | Relaxed interception ceiling |
| `adv` | Confirmation dialog with an optional remembered choice (argon2id password required) |

## Path-level Permissions

`etc/perm_path.json` defines path-level permission rules. The same `rm` can be allowed under `/tmp`
and blocked under `/etc` — something ordinary filesystem permissions cannot express.

Example rule:

```json
"/etc/<*:10>": {
  "mode": "whitelist",
  "allow_advanced_syntax": false,
  "commands": ["ls", "cd", "cat", "grep"]
}
```

## Dangerous Command Blacklist

`etc/dan_cmd` lists interception patterns, one per line — `rm -rf /`, `mkfs`, `dd if=/dev/zero` and
100+ more.

## AI Features

The `ai` command talks to the backend over an SSE API. The returned commands are fed one by one
through the full parse → security-check → execute pipeline. Because the PTY session is persistent,
the AI's `cd /tmp` takes effect for the next command.

### API Key

The first time you run `ai`, an interactive wizard asks for the platform and API key. You can also
set or change the key later:

- `ai -key <API Key>` — set the key for the configured platform (non-interactive)
- Inside AI chat: `/key` to view / change the key, `/config` for the full menu (platform / model / key / params / URL)

Keys are stored obfuscated with `0600` permissions in `~/.config/onyx/ai/key.json`; stray
whitespace and newlines are stripped on input and on read.

### Interactive Modes (REPL / TUI)

Bare `ai` enters the default interactive mode; override it explicitly:

- `ai` — default mode (one variable: `bin/ai_lib/mode.py` → `DEFAULT_AI_MODE`)
- `ai -repl` — line-based REPL
- `ai -tui` — full-screen TUI: a fixed input box at the bottom (still usable while the AI runs —
  typed messages queue as live guidance), a chat pane, and — on wide terminals — side panels
  (TODO + files). Dangerous-command confirmations and captchas appear as modal dialogs.

## Proactive Perception (Senses)

The AI can set **visual attentions** — its "eyes" — on slices of files or on tool results. A
background *senses* thread (independent from the main thread) watches them. When a file is changed
**externally** and stays quiet for `debounce_sec` (default 3s), the AI is woken once **while idle**
with the before/after content attached. Files the AI changes itself (`write_file` / `edit_file` /
`UndoLastEdit`) update the vision document but never trigger a wake-up.

- Toggle: `/config` → 👁 Proactive perception (off by default)
- The first time the AI sets an attention, a confirmation dialog explains that information may be
  sent to the AI while you are not chatting — **this consumes your tokens**; approve once, never asked again
- Commands: `/senses list` (attentions the AI has set), `/senses on` / `/senses off`, `/senses clear`
- Vision content is appended to the bottom of each request's dynamic context and is **never**
  written into conversation memory; rate limits: `min_interval_sec` 60s, `max_per_hour` 6
- Sub-agents neither share the vision nor can they set attentions
- Credential files (`.ssh/`, `id_rsa`, `.env`, `*.pem`, `credentials`, …) and binaries are
  refused: vision content is sent to the model API, so secrets never enter it

## Computer Use (The AI's Hands)

On Windows / Linux the AI can drive the real desktop: screenshot, read the accessibility tree,
click, type, press keys, scroll, drag, manage windows and launch apps. On Android only a
lightweight `launch` survives — without root, `screencap` / `input` / `uiautomator` need shell
privileges.

- `ui_tree` returns a **text** control tree (`[3] push button "Save" rect=… center=…`); `find`
  filters it by name / role; `click` accepts `control=<n>` (the index) — steadier than raw coordinates
- `screenshot` writes a PNG under `~/.ai_s/computer_use/shots/` and returns path / size / dims /
  sha1 / changed — the image itself is **not** sent to the model (tool results are text-only)
- Backends: Windows (ctypes `user32` + PowerShell capture + UIA / Win32 tree), Linux X11
  (`xdotool` + `scrot`/`maim` + `wmctrl`, optional `pyatspi`), Linux Wayland (`grim` +
  `ydotool`/`wtype` + `swaymsg`), Android (`am start` only)
- Zero hard dependencies: missing tools are reported as `error:` with the install hint;
  `safe_mode` blocks destructive combos such as Ctrl+Alt+Delete
- Config: `~/.ai_s/computer_use/config.json` (`backend`, `shots_dir`, `timeout`,
  `max_tree_nodes`, `max_depth`, `safe_mode`, …)

## Ghost Completion

Based on command frequency: as you type, the most likely completion appears in grey directly after
the cursor — no Tab needed.

## Project Structure

```
Onyx.py                   Terminal main program (REPL, all builtins)
Main.py                   Launcher (environment check + cache)
cmd.py                    Single-command executor
lib/terminal/exe.py       PTY persistent shell
lib/safe.py               Security module (perm_path.json, dan_cmd, three-mode confirmation)
lib/parse.py              Shell parser (bash/zsh/fish/cmd/powershell)
lib/parse_and_execute.py  Command dispatch backbone
bin/                      Builtin command implementations
etc/                      Runtime configuration
```

---

# Onyx — 帮助文档

**中文** · English version above ↑

Onyx 是一个用 Python 编写的终端模拟器、Shell 与安全沙箱。语法高亮、幽灵补全、安全拦截和 AI
内建命令都实现在输入层；真正执行命令的 shell，是通过 PTY 连接的真实 `bash`/`zsh`。

## 目录

- [快速入门](#快速入门)
- [架构](#架构)
- [内建命令](#内建命令)
- [子命令](#子命令)
- [语言切换](#语言切换)
- [MCP](#mcp-model-context-protocol)
- [任务系统](#任务系统)
- [TBS 与 OS 模式](#tbs-与-os-模式)
- [安全模式](#安全模式)
- [路径级权限](#路径级权限)
- [高危命令黑名单](#高危命令黑名单)
- [AI 功能](#ai-功能)
- [主动感知（感官 / 视觉）](#主动感知感官--视觉)
- [幽灵补全](#幽灵补全)
- [项目结构](#项目结构)

## 快速入门

用脚本装好 Onyx 后，在终端里直接输入 `onyx` 即可启动。

1. **首次启动** —— Onyx 会要求你设置**高级模式（`adv`）的密码**。
2. **沙箱** —— 随后它会创建一个*虚拟根目录*：一个模拟 `/` 的目录，把你的真实文件系统隔离在外。
   用 `manage set sandbox false` 可以关闭它。
3. **日常使用** —— 平时可以把它当作一个普通的 shell 来用。
4. **安全网** —— 可能造成破坏的命令会被拦截，必须先确认（输入密码或验证码）才会执行。
5. **AI** —— 输入 `ai` 进入 AI 模式；首次使用需要先配置后端。AI 可以帮你改代码、敲命令，
   完成一系列复杂的多步任务。

## 架构

```
键盘输入 → Onyx（输入 · 解析 · 安全 · AI）→ PTY → bash/zsh → 内核
```

Onyx 不替换你的 shell：它接管输入层，把真正的执行交给 `bash`/`zsh`。

## 内建命令

| 命令 | 说明 |
|------|------|
| `exit` | 退出 Onyx |
| `refresh` | 刷新工具索引 |
| `export <VAR>=<value>` | 设置环境变量 |
| `activite` | 激活 / 切换安全模式（`low` / `mid` / `adv`） |
| `manage <subcommand>` | 管理配置（读取 / 设置配置项） |
| `switch-prompt <style>` | 切换提示符风格（`kali` / `ubuntu` / `zsh` / `onyx` / `termux` / `def` / `skali`） |
| `ai <prompt>` | AI 助手（需平台 API Key） |
| `ai -key <API Key>` | 为当前平台设置 API Key（存入 `~/.config/onyx/ai/key.json`） |
| `ai config <子命令>` | 不进交互菜单改私人配置：`show` / `get` / `set` / `test` / `doctor` / `backup` / `restore` / `path` |
| `ai -repl` / `ai -tui` | 强制交互模式（REPL / 全屏 TUI）；裸 `ai` 走默认模式 |
| `set-adv-pwd` | 设置高级模式密码 |
| `help` | 显示本帮助 |
| `mktool -n <工具名> -l <语言>` | 创建新的工具箱插件工具，自动生成配置 / 权限文件。支持语言：python / c / cpp / bash；工具路径：`tools/plugin/<工具名>`；示例：`mktool -n port_scanner -l python` |
| `sado <cmd>` | 以高级权限运行命令（类似 sudo，走 Onyx 权限系统） |
| `nanosado` | 轻量权限提升 |
| `history` | 查看命令历史 |

> `activite` 是历史遗留拼写（为兼容保留），它的作用是*激活*安全模式。

## 子命令

### `manage` — 配置管理

| 子命令 | 说明 |
|------|------|
| `manage set <key> <value>` | 设置配置项 |
| `manage get <key>` | 读取配置项 |
| `manage set language english` | 切换为英文界面 |
| `manage set language chinese` | 切换为中文界面 |
| `manage set sandbox false` | 关闭虚拟根沙箱 |
| `manage set debug-times true` | 显示每条命令的耗时 |
| `manage set debug-parsecmd true` | 启用命令解析调试输出 |
| `manage set clean-log-time <days>` | 设置日志自动清理天数 |
| `manage shell <name>` | 设定**输入模块与底层 PTY 共用**的 shell（bash/zsh/fish/sh/pwsh/cmd 或路径） |
| `manage shell` | 查看当前 shell |

### `activite` — 安全模式

| 参数 | 说明 |
|------|------|
| `activite -m low` | 严格模式，仅允许白名单命令 |
| `activite -m mid` | 适中模式，放宽拦截上限 |
| `activite -m adv` | 高级模式，弹框确认 + argon2id 密码验证 |

### `switch-prompt` — 提示符风格

| 风格 | 说明 |
|------|------|
| `switch-prompt kali` | Kali Linux 风格 |
| `switch-prompt ubuntu` | Ubuntu 风格 |
| `switch-prompt zsh` | Zsh 风格 |
| `switch-prompt onyx` | Onyx 默认风格 |
| `switch-prompt termux` | Termux 风格 |
| `switch-prompt def` | 默认简约风格 |
| `switch-prompt skali` | 简化版 Kali 风格 |

## 语言切换

Onyx 界面支持中英双语，可随时切换：

| 命令 | 作用 |
|------|------|
| `manage set language english` | 切换为英文界面 |
| `manage set language chinese` | 切换为中文界面 |

- 选择会持久保存到 `~/.config/onyx/language`，重启后依然有效。
- 作用范围：提示符、帮助文本、错误提示以及 AI 模块。
- 默认值定义在 `etc/config.json` 的 `display_info.language`（默认 `Chinese`，
  支持 `Chinese`、`English`）。

## MCP（Model Context Protocol）

Onyx 支持通过 MCP 集成 AI 工具：

- `etc/mcp/mcp.json` —— MCP 服务器配置
- `bin/ai_lib/mcp_client.py` —— MCP 客户端实现
- `bin/ai_lib/mcp_registry.py` —— MCP 工具注册表
- `bin/ai_lib/mcp_transport.py` —— 传输层（SSE / stdio）

MCP 让 AI 可以通过标准协议调用外部工具，扩展它的能力边界。

## 任务系统

Onyx 内置任务系统（`lib/task_system/`）：

- **Task Registry** —— 任务注册与管理
- **Cron Registry** —— 定时任务调度
- **Team Registry** —— 团队协作任务
- **Task Packet** —— 结构化任务定义

## TBS 与 OS 模式

| 模式 | 行为 |
|------|------|
| **TBS** | 虚拟沙箱 —— 所有操作都限制在虚拟根目录内，不影响真实系统。 |
| **OS** | 完整系统访问 —— 虚拟根目录*就是*系统根目录，可自由读写。 |

Onyx 会自动判断使用哪种模式：当 `ROOT_DIR` 等于系统根目录（`/` 或 `C:\`）时，即进入 OS 模式。

## 安全模式

| 模式 | 说明 |
|------|------|
| `low` | 严格拦截，仅允许白名单命令 |
| `mid` | 放宽拦截上限 |
| `adv` | 弹框确认，可选择记住本次选择（需 argon2id 密码验证） |

## 路径级权限

`etc/perm_path.json` 定义路径级权限规则。同一个 `rm` 命令在 `/tmp` 下可以执行、在 `/etc` 下
被拦截 —— 这是传统文件系统权限做不到的。

规则示例：

```json
"/etc/<*:10>": {
  "mode": "whitelist",
  "allow_advanced_syntax": false,
  "commands": ["ls", "cd", "cat", "grep"]
}
```

## 高危命令黑名单

`etc/dan_cmd` 定义拦截模式，一行一条，包含 `rm -rf /`、`mkfs`、`dd if=/dev/zero` 等 100+ 条规则。

## AI 功能

`ai` 命令通过 SSE API 与后端通信，返回的命令列表会逐条走完整个「解析 → 安全检查 → 执行」管线。
由于 PTY 会话是持久的，AI 执行的 `cd /tmp` 会对下一条命令生效。

### API Key 配置

首次运行 `ai` 会进入交互式配置向导，也可稍后设置 / 更换密钥：

- `ai -key <API Key>` —— 非交互式为已配置平台设置密钥
- AI 对话内：`/key` 查看 / 更换密钥，`/config` 打开完整菜单（平台 / 模型 / 密钥 / 参数 / URL）

密钥以混淆形式存于 `~/.config/onyx/ai/key.json`（权限 0600）；输入与读取时都会自动去除首尾空白与换行。

### 交互模式（REPL / TUI）

裸 `ai` 进入默认交互模式，可显式覆盖：

- `ai` —— 默认模式（单一变量：`bin/ai_lib/mode.py` 的 `DEFAULT_AI_MODE`）
- `ai -repl` —— 行式 REPL
- `ai -tui` —— 全屏 TUI：底部固定输入框（AI 运行时仍可输入，消息排队作为实时引导）、
  对话面板，宽终端下显示侧栏（TODO + 文件）。危险命令确认与验证码以模态框弹出。

## 主动感知（感官 / 视觉）

AI 可以给自己设「视觉注意力」—— 它的「眼睛」：盯住文件的某些行段，或某个工具的返回值。
一条**独立的后台感官线程**（与主线程完全分离）实时监视它们。当文件被**外部**改动、并且静默
`debounce_sec`（默认 3 秒）后，会在你没和 AI 对话时主动唤醒它一次，并附上改前/改后内容。
AI 自己用 `write_file` / `edit_file` / `UndoLastEdit` 改的文件只更新视觉文档，**不会**触发唤醒。

- 开关：`/config` → 👁 主动感知（默认关闭）
- AI 第一次设置注意力时会弹窗确认：开启后可能在你没有对话时把信息发给 AI，**会消耗 Token**；
  同意一次后不再询问
- 命令：`/senses list`（看 AI 设了哪些注意力）、`/senses on` / `/senses off`、`/senses clear`
- 视觉内容只拼在每次请求动态上下文的末尾，**不写入**会话记忆；限流：间隔 60 秒、每小时最多 6 次
- 子代理既不共享视觉，也不能设置注意力
- 凭证类文件（`.ssh/`、`id_rsa`、`.env`、`*.pem`、`credentials` 等）与二进制文件会被拒绝：
  视觉内容会发送给模型 API，密钥不允许进入视觉

## 桌面操控（AI 的手）

Windows / Linux 上，AI 可以直接操作桌面：截图、读无障碍控件树、点击、输入、按键、滚动、拖拽、
管理窗口、启动应用。Android 上只保留轻量 `launch`（`am start`）—— 无 root 时 `screencap` /
`input` / `uiautomator` 都需要 shell 权限。

- `ui_tree` 返回**文本**控件树（`[3] push button "Save" rect=… center=…`），`find` 按名称/角色
  过滤，`click` 支持 `control=<n>` 按索引点击 —— 比裸坐标稳
- `screenshot` 把 PNG 存到 `~/.ai_s/computer_use/shots/`，只返回路径/大小/尺寸/sha1/是否变化，
  图片本身**不会**发给模型（工具结果只支持纯文本）
- 后端：Windows（ctypes `user32` + PowerShell 截图 + UIA / Win32 控件树）、Linux X11
  （`xdotool` + `scrot`/`maim` + `wmctrl`，可选 `pyatspi`）、Linux Wayland（`grim` +
  `ydotool`/`wtype` + `swaymsg`）、Android（仅 `am start`）
- 零硬依赖：缺命令时返回 `error:` 并给出安装提示；`safe_mode` 拦截 Ctrl+Alt+Delete 等高危组合键
- 配置：`~/.ai_s/computer_use/config.json`（`backend`、`shots_dir`、`timeout`、
  `max_tree_nodes`、`max_depth`、`safe_mode` 等）

## 幽灵补全

基于命令频率：在你输入的同时，概率最高的补全建议会以灰色文本直接显示在光标后面 —— 不需要按 Tab。

## 项目结构

```
Onyx.py                   终端主程序（REPL、全部内建命令）
Main.py                   启动器（环境检查 + 缓存）
cmd.py                    单命令执行器
lib/terminal/exe.py       PTY 持久 shell
lib/safe.py               安全模块（perm_path.json、dan_cmd、三模式确认）
lib/parse.py              Shell 解析器（bash/zsh/fish/cmd/powershell）
lib/parse_and_execute.py  命令调度主干
bin/                      内建命令实现
etc/                      运行时配置
```
