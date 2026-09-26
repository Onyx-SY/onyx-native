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
