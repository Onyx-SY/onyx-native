<p align="center">
  <img src="https://img.shields.io/badge/python-3.8+-blue" alt="Python">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  <img src="https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Termux-lightgrey" alt="Platform">
</p>

<h1 align="center">Onyx-native</h1>
<h3 align="center">Terminal Emulator · Shell · Security Sandbox · AI Agent</h3>

<p align="center"><a href="https://onyxsy.com"><b>onyxsy.com</b></a></p>

Onyx is a terminal emulator, command shell, mandatory security layer, and AI agent — all running in
a single Python process. It owns the keyboard, parses every command before it reaches the PTY,
enforces path-level permissions, and lets an AI control your terminal through the exact same
parse → security → execute pipeline as a human user.

```
Traditional:       Terminal  →  Shell (bash)  →  Kernel
Other "AI shells":  Terminal  →  Shell  →  AI tool-calling wrapper

Onyx:  Onyx (Input + Parse + Security + AI)  →  PTY  →  bash  →  Kernel
       ↑ All four layers live in the same process ↑
```

---

## 🏗️ Architecture

Every command you type goes through a single pipeline:

```
universal_input()           prompt_toolkit keyboard capture, ghost completion, syntax highlighting
        ↓
parse_and_execute()         classify: builtin / tool / system / unknown
        ↓
security check              3-mode + perm_path.json + dan_cmd blacklist + syntax control
        ↓
run_cmd_sync()              persistent PTY → bash/zsh (passthrough mode, raw TTY)
        ↓
_sync_cwd_from_shell()      keep Python CWD in sync with PTY
```

### Input Layer (`lib/terminal/`)

| Component | File | What it does |
|---|---|---|
| Prompt & Input | `input_lib.py` | prompt_toolkit REPL, history, multi-line, 8 prompt styles |
| Completion | `com.py` | Ghost completion (grey inline text), path completion, syntax highlighting |
| PTY Engine | `exe.py` | `PersistentShell` — long-lived bash/zsh, passthrough mode, CWD sync, TTY raw mode |
| Colors | `colors.py` | Terminal color utilities |

### Command Pipeline (`lib/parse_and_execute.py`)

| Step | What happens |
|---|---|
| 1. Alias resolve | Expand registered aliases |
| 2. Classify | `builtin` > `other_terminal` > `tool` > `system` > `unknown` |
| 3. Security | Check mode, path permissions, blacklist, syntax restrictions |
| 4. Execute | `_execute_command_unified()` → PTY passthrough (system) or Python call (builtin) |
| 5. CWD Sync | Cooldown-gated PTY CWD read → `os.chdir()` |

**Builtins** are Python functions in `Onyx.py`'s `BUILTIN_COMMANDS` dict — no PTY needed.
**Tools** are executables discovered under the tools/ directory with permission levels 1-3.
**System commands** go straight to bash/zsh via PTY passthrough.
**Unknown commands** are forwarded to bash — it decides "command not found" or executes.

### PTY Engine (`lib/terminal/exe.py`)

A single long-lived PTY session (bash/zsh/fish/pwsh) is created at boot and reused for all commands.
Two execution modes:

- **Passthrough** (system commands): raw TTY, command + stderr exit-code marker, real-time
  stdout forwarding, stdin passthrough for TUI programs (vim, top, nano)
- **Wrapped** (tools): markers for output extraction, CWD reading, variable queries

---

## 🔒 Security Model

Security runs **before** the command reaches bash — not post-audit.

### Three Modes

| Mode | Behavior |
|---|---|
| `low` | Strict block — unknown commands refused, dangerous patterns intercepted |
| `mid` | Higher ceiling — more commands allowed, some paths unlocked |
| `adv` | Dialog confirm — popup with random captcha, choice can be remembered |

### Path-Level Permissions (`etc/perm_path.json`)

The SAME command can be allowed or blocked depending on WHERE it runs:

```json
{
  "/home/user":  { "allow": ["ls", "cat", "echo"], "deny": ["rm", "mv"] },
  "/tmp":        { "allow": ["rm", "mv", "cp"] },
  "/etc":        { "allow": [], "deny": ["*"] }
}
```

This is something file permissions (chmod) cannot express.

### Blacklist (`etc/dan_cmd`)

100+ patterns auto-detected: `rm -rf /`, `mkfs`, `dd if=/dev/zero`, fork bombs, chmod 777, etc.

### Syntax Control

Pipes, redirects (`>`, `>>`, `<`), here-documents (`<<`), and logical operators (`&&`, `||`) can
be individually forbidden in fine-grained paths — preventing bypass through `cat /etc/shadow | nc`.

---

## 🤖 AI Agent

The `ai` command is registered as a **builtin** — same standing as `cd`, `export`, `clear`. It's
not bolted on top; it's inside the dispatch loop.

### Multi-Model

Supports DeepSeek, OpenAI, Ollama, Claude, Gemini, Grok, xAI, GitHub Copilot, and any
OpenAI-compatible endpoint. Streaming via SSE with real-time Markdown rendering.

### How It Works

```
ai "find what's using port 8080 and kill it"
  → SSE API (carries OS, shell type, CWD, tool list, current mode, memory context)
  → AI returns structured commands: ["lsof -i :8080", "kill <PID>"]
  → Each command goes through parse_and_execute()
  → Same security checks as a human user
  → Same PTY session → cd /tmp persists for the NEXT command
  → Output captured and returned to AI for next decision
```

The AI doesn't call "tools" — it **uses the computer**. It can `apt install`, then immediately
run what it installed. No preset wrappers.

### MCP Protocol

Built-in MCP (Model Context Protocol) support — connects to MCP servers for filesystem access,
memory, and other capabilities. Tools are schema-cached with fingerprint validation.

### Session & Memory

| Type | Path | Purpose |
|---|---|---|
| Chat history | `.ai_s/chat/{name}.json` | Multi-turn dialog, grouped by chat name |
| Session records | `.ai_s/library/{id}.txt` | Per-session full output & context |
| Current chat | `.ai_s/chat.txt` | Active chat name pointer |

---

## 🚀 Quick Start

```bash
pip install prompt_toolkit colorama rich requests msgpack

python3 Onyx.py           # Start the terminal
python3 Main.py -l        # Login mode (simulated login shell)
python3 cmd.py "ls -la"   # Single command execution
python3 man.py            # Rebuild man-page index
```

---

## 📁 Project Structure

| Path | Role |
|---|---|
| `Onyx.py` | Main program — 35-step boot, REPL loop, all builtins |
| `Main.py` | Launcher — environment check, cache warmup |
| `cmd.py` | Single-command executor |
| `lib/terminal/exe.py` | Persistent PTY shell (passthrough, CWD sync, marker extraction) |
| `lib/terminal/input_lib.py` | prompt_toolkit input, history, multi-line |
| `lib/terminal/com.py` | Ghost completion, path completion, syntax highlighting |
| `lib/parse_and_execute.py` | Command dispatch backbone — classify → security → execute |
| `lib/parse.py` | Shell parser (bash/zsh/fish/cmd/powershell) |
| `lib/safe.py` | Security — modes, perm_path.json, dan_cmd, fine-grained control |
| `bin/ai_cmd.py` | AI agent — SSE streaming, MCP, memory, multi-model |
| `bin/` | Other builtins — manage, run, sado, activite, export, … |
| `core/` | Shared modules — context, i18n, config loader, path ops |
| `etc/` | Runtime config — config.json, perm_path.json, dan_cmd, … |
| `lib/c/` | ARM64 C extensions for path resolution, caching, scanning |

---

## 📄 License

MIT — see [LICENSE](LICENSE)

---

---

# Onyx-native

### 终端模拟器 · Shell · 安全沙箱 · AI Agent

<p align="center"><a href="https://onyxsy.com"><b>onyxsy.com</b></a></p>

Onyx 是一个终端模拟器、命令 Shell、强制安全层和 AI Agent —— 全部跑在一个 Python 进程里。
它掌控键盘输入，在命令到达 PTY 之前完成解析和安全检查，执行路径级权限控制，并让 AI 通过
**和人类用户完全相同的** 解析 → 安全 → 执行管线来操控你的终端。

```
传统架构：        终端  →  Shell (bash)  →  内核
其他 "AI Shell"： 终端  →  Shell  →  AI 工具调用封装

Onyx：  Onyx（输入 + 解析 + 安全 + AI）  →  PTY  →  bash  →  内核
        ↑ 四层全在同一个进程里 ↑
```

---

## 🏗️ 架构

每条命令都经过同一个管线：

```
universal_input()           prompt_toolkit 键盘捕获、幽灵补全、语法高亮
        ↓
parse_and_execute()         分类：builtin / tool / system / unknown
        ↓
安全检查                    三级模式 + perm_path.json + dan_cmd 黑名单 + 语法控制
        ↓
run_cmd_sync()              持久 PTY → bash/zsh（passthrough 模式、raw TTY）
        ↓
_sync_cwd_from_shell()      保持 Python CWD 与 PTY 同步
```

### 输入层 (`lib/terminal/`)

| 组件 | 文件 | 功能 |
|---|---|---|
| 提示符与输入 | `input_lib.py` | prompt_toolkit REPL、历史记录、多行、8 种 prompt 风格 |
| 补全 | `com.py` | 幽灵补全（灰色内联文字）、路径补全、语法高亮 |
| PTY 引擎 | `exe.py` | `PersistentShell` — 长生命周期 bash/zsh、passthrough、CWD 同步 |
| 颜色 | `colors.py` | 终端颜色工具 |

### 命令管线 (`lib/parse_and_execute.py`)

| 步骤 | 动作 |
|---|---|
| 1. 别名解析 | 展开已注册的别名 |
| 2. 分类 | `builtin` > `other_terminal` > `tool` > `system` > `unknown` |
| 3. 安全 | 检查模式、路径权限、黑名单、语法限制 |
| 4. 执行 | `_execute_command_unified()` → PTY passthrough（系统命令）或 Python 调用（builtin） |
| 5. CWD 同步 | 冷却门控的 PTY CWD 读取 → `os.chdir()` |

**Builtin** 是 `Onyx.py` 里 `BUILTIN_COMMANDS` 字典中的 Python 函数，不走 PTY。
**Tool** 是 tools/ 目录下发现的带权限级别 (1-3) 的可执行文件。
**System** 命令直接通过 PTY passthrough 发给 bash/zsh。
**Unknown** 命令转发给 bash —— 由它决定 "command not found" 或执行。

### PTY 引擎 (`lib/terminal/exe.py`)

启动时创建一个长生命周期的 PTY session (bash/zsh/fish/pwsh)，所有命令复用它。两种执行模式：

- **Passthrough**（系统命令）：raw TTY，命令 + stderr 退出码标记，实时 stdout 转发，
  stdin 透传支持 TUI 程序（vim、top、nano）
- **Wrapped**（工具）：标记提取输出、CWD 读取、变量查询

---

## 🔒 安全模型

安全检查在命令**到达 bash 之前**执行 —— 不是事后审计。

### 三级模式

| 模式 | 行为 |
|---|---|
| `low` | 严格拦截 —— 未知命令拒绝，危险模式拦截 |
| `mid` | 更高上限 —— 更多命令允许，部分路径解锁 |
| `adv` | 弹框确认 —— 随机验证码弹窗，可选择记住 |

### 路径级权限 (`etc/perm_path.json`)

同一个命令在不同路径下**可以有不同的权限**：

```json
{
  "/home/user":  { "allow": ["ls", "cat", "echo"], "deny": ["rm", "mv"] },
  "/tmp":        { "allow": ["rm", "mv", "cp"] },
  "/etc":        { "allow": [], "deny": ["*"] }
}
```

这是文件权限 (chmod) 做不到的。

### 黑名单 (`etc/dan_cmd`)

100+ 条自动检测模式：`rm -rf /`、`mkfs`、`dd if=/dev/zero`、fork 炸弹、chmod 777 等。

### 语法控制

管道、重定向 (`>`、`>>`、`<`)、here-document (`<<`)、逻辑操作符 (`&&`、`||`) 可以在
细颗粒度路径中被单独禁止 —— 防止通过 `cat /etc/shadow | nc` 之类的手法绕过。

---

## 🤖 AI Agent

`ai` 命令是一个 **builtin** —— 和 `cd`、`export`、`clear` 地位平等。它不是外挂的，它在调度循环里面。

### 多模型支持

支持 DeepSeek、OpenAI、Ollama、Claude、Gemini、Grok、xAI、GitHub Copilot 及任何
OpenAI 兼容端点。SSE 流式传输，实时 Markdown 渲染。

### 工作原理

```
ai "找到占用 8080 端口的进程并杀掉"
  → SSE API（携带 OS、shell 类型、CWD、工具列表、当前模式、记忆上下文）
  → AI 返回结构化命令：["lsof -i :8080", "kill <PID>"]
  → 每条命令经过 parse_and_execute()
  → 和人类用户一样的安全检查
  → 同一个 PTY session → cd /tmp 对下一条命令生效
  → 输出捕获后返回给 AI 做下一步决策
```

AI 不是「调用工具」—— 它是**真正使用电脑**。它可以 `apt install` 然后立刻运行刚装好的程序。
不需要预设封装。

### MCP 协议

内建 MCP（Model Context Protocol）支持 —— 连接 MCP 服务器获取文件系统、记忆等能力。
工具 schema 带指纹验证缓存。

### Session 与记忆

| 类型 | 路径 | 用途 |
|---|---|---|
| 对话历史 | `.ai_s/chat/{name}.json` | 按 chat 分组的多轮对话 |
| Session 记录 | `.ai_s/library/{id}.txt` | 每次 session 的完整输出与上下文 |
| 当前 Chat | `.ai_s/chat.txt` | 当前活跃的 chat 名称 |

---

## 🚀 快速开始

```bash
pip install prompt_toolkit colorama rich requests msgpack

python3 Onyx.py           # 启动终端
python3 Main.py -l        # 登录模式（模拟 login shell）
python3 cmd.py "ls -la"   # 单次命令执行
python3 man.py            # 重建 man-page 索引
```

---

## 📁 项目结构

| 路径 | 职责 |
|---|---|
| `Onyx.py` | 主程序 —— 35 步启动、REPL 循环、全部 builtin |
| `Main.py` | 启动器 —— 环境检查、缓存预热 |
| `cmd.py` | 单命令执行器 |
| `lib/terminal/exe.py` | 持久 PTY Shell（passthrough、CWD 同步、标记提取） |
| `lib/terminal/input_lib.py` | prompt_toolkit 输入、历史、多行 |
| `lib/terminal/com.py` | 幽灵补全、路径补全、语法高亮 |
| `lib/parse_and_execute.py` | 命令调度主干 —— 分类 → 安全 → 执行 |
| `lib/parse.py` | Shell 解析器（bash/zsh/fish/cmd/powershell） |
| `lib/safe.py` | 安全模块 —— 模式、perm_path.json、dan_cmd、细颗粒控制 |
| `bin/ai_cmd.py` | AI Agent —— SSE 流式、MCP、记忆、多模型 |
| `bin/` | 其余 builtin —— manage、run、sado、activite、export … |
| `core/` | 共享模块 —— context、i18n、配置加载、路径操作 |
| `etc/` | 运行时配置 —— config.json、perm_path.json、dan_cmd … |
| `lib/c/` | ARM64 C 扩展（路径解析、缓存、扫描） |

---

## 📄 许可

MIT — 详见 [LICENSE](LICENSE)
