<p align="center">
  <img src="https://img.shields.io/badge/python-3.8+-blue" alt="Python">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  <img src="https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Termux-lightgrey" alt="Platform">
</p>

<h1 align="center">Onyx-native</h1>
<h3 align="center">Terminal Emulator · Shell · Security Layer · AI Agent</h3>

<p align="center"><a href="https://onyxsy.com"><b>onyxsy.com</b></a></p>

> **A native shell environment where humans and AI share the same secure execution layer.**
> **一个让人类和 AI 共享同一安全执行层的新一代 Shell。**

```
Traditional:       Terminal  →  Shell (bash)  →  Kernel
Other "AI shells":  Terminal  →  Shell  →  AI tool-calling wrapper

Onyx:  Onyx (Input + Parse + Security + AI)  →  PTY  →  bash  →  Kernel
       ↑ All four layers live in the same process ↑
```

---

## 🖥️ Modern Shell Experience

Even without AI, Onyx is a full-featured terminal you'll want to use every day.

| Feature | What you get |
|---|---|
| **Ghost completion** | Grey inline text predicts your next command — press → to accept |
| **Syntax highlighting** | Commands, paths, strings, and operators highlighted as you type |
| **Multi-line editing** | Full multi-line input with navigation, not a single-line box |
| **8 prompt styles** | Switch between minimal, powerline, arrow, double-line, and more |
| **Smart history** | Per-directory history, fuzzy search, deduplication |
| **Path completion** | Tab-complete paths with visual preview |
| **TUI passthrough** | vim, top, nano, htop — run interactive programs naturally |
| **CWD sync** | `cd` in the PTY is reflected in Onyx's working directory in real time |

All built on top of `prompt_toolkit` with a persistent PTY session underneath.

---

## 🔌 PTY Engine

A single long-lived PTY session (bash, zsh, fish, or PowerShell) is created at boot and reused
for every command — both yours and the AI's.

Two execution modes share the same session:

- **Passthrough** — raw TTY, real-time stdout forwarding, stdin passthrough.  Used for system
  commands and interactive TUI programs.
- **Wrapped** — markers for output extraction, CWD reading, and variable queries.  Used by
  builtins and tools that need structured output.

This means `cd /tmp` from any source (you, a script, the AI) persists for every subsequent
command.  One terminal, one session, one shared reality.

---

## 🔒 Security Model

Security runs **before** the command reaches bash — not post-audit, not a sandbox wrapper.

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

### Blacklist & Syntax Control

100+ dangerous patterns auto-detected (`rm -rf /`, `mkfs`, fork bombs, `chmod 777`).
Pipes, redirects, here-documents, and logical operators can each be individually forbidden
at fine-grained path levels — preventing bypass via `cat /etc/shadow | nc`.

---

## 🤖 AI Agent

The `ai` command is a **builtin** — same standing as `cd`, `export`, and `clear`.  The AI
operates through Onyx's execution pipeline instead of bypassing the terminal runtime.

### Multi-Model

Supports DeepSeek, OpenAI, Ollama, Claude, Gemini, Grok, xAI, GitHub Copilot, and any
OpenAI-compatible endpoint.  Streaming via SSE with real-time Markdown rendering.
Model list is maintained in `etc/ai/models.json` — edit it to add or change models without
touching code.

### Execution Pipeline

```
ai "find what's using port 8080 and kill it"
  → SSE API (carries OS, shell type, CWD, tool list, mode, memory context)
  → AI returns structured commands: ["lsof -i :8080", "kill <PID>"]
  → Each command goes through parse_and_execute()
  → Same security checks as a human — nothing bypasses the security layer
  → Same PTY session → state is real and shared
  → Output captured and returned to AI for the next decision
```

The AI can `apt install` and immediately run what it installed.  No preset tool wrappers.

### MCP Protocol

Built-in MCP (Model Context Protocol) support — connects to MCP servers for filesystem access,
memory, and other capabilities.  Tool schemas are cached with fingerprint validation.

---

## 🧠 Cognitive Memory System

Onyx maintains persistent memory across sessions, not just chat logs.

| Layer | Path | Purpose |
|---|---|---|
| **Supreme Directives** | `.ai_s/onyx_ai.md` | Cross-session persistent instructions the AI self-maintains via `[PROMPT]:` |
| **Chat history** | `.ai_s/chat/{name}.json` | Multi-turn dialogs grouped by chat name, full context preserved |
| **Session records** | `.ai_s/library/{id}.txt` | Complete per-session output, context, and decisions |
| **Episodic memory** | `.ai_s/memory/` | Key events and facts extracted from sessions, with proactive forgetting |


The AI writes to `.ai_s/onyx_ai.md` autonomously to record decisions and preferences that
persist across restarts.  Episodic memory uses a JSON backbone with automatic pruning — old
or low-importance entries fade out over time.

---

## 🏗️ Architecture

Every command goes through a single pipeline:

```
universal_input()           prompt_toolkit keyboard capture
        ↓
parse_and_execute()         classify: builtin / tool / system / unknown
        ↓
security check              mode + perm_path + dan_cmd + syntax control
        ↓
run_cmd_sync()              persistent PTY → bash / zsh
        ↓
_sync_cwd_from_shell()      keep Python CWD in sync with PTY
```

| Layer | Path | Role |
|---|---|---|
| Input | `lib/terminal/input_lib.py` | prompt_toolkit REPL, history, multi-line, prompt styles |
| Completion | `lib/terminal/com.py` | Ghost completion, path completion, syntax highlighting |
| PTY Engine | `lib/terminal/exe.py` | PersistentShell — passthrough, CWD sync, TTY raw mode |
| Dispatch | `lib/parse_and_execute.py` | classify → security → execute backbone |
| Parser | `lib/parse.py` | Shell parser (bash/zsh/fish/cmd/powershell) |
| Security | `lib/safe.py` | Modes, perm_path.json, dan_cmd, fine-grained control |
| AI Agent | `bin/ai_cmd.py` | SSE streaming, MCP, memory, multi-model |
| Config | `etc/` | config.json, perm_path.json, dan_cmd, ai/models.json |
| C Extensions | `lib/c/` | ARM64 C modules for path resolution, caching, scanning |

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

## 📄 License

MIT — see [LICENSE](LICENSE)

---

---

# Onyx-native

### 终端模拟器 · Shell · 安全层 · AI Agent

<p align="center"><a href="https://onyxsy.com"><b>onyxsy.com</b></a></p>

> **一个让人类和 AI 共享同一安全执行层的新一代 Shell。**
> **A native shell environment where humans and AI share the same secure execution layer.**

```
传统架构：        终端  →  Shell (bash)  →  内核
其他 "AI Shell"： 终端  →  Shell  →  AI 工具调用封装

Onyx：  Onyx（输入 + 解析 + 安全 + AI）  →  PTY  →  bash  →  内核
        ↑ 四层全在同一个进程里 ↑
```

---

## 🖥️ 现代 Shell 体验

即使不用 AI，Onyx 也是一个值得日常使用的全功能终端。

| 功能 | 说明 |
|---|---|
| **幽灵补全** | 灰色内联文字预测你的下一条命令，按 → 接受 |
| **语法高亮** | 命令、路径、字符串、操作符实时着色 |
| **多行编辑** | 完整的多行输入与导航，不是单行输入框 |
| **8 种 prompt 风格** | 自由切换：极简、powerline、箭头、双行等 |
| **智能历史** | 按目录分组历史、模糊搜索、自动去重 |
| **路径补全** | Tab 补全路径，带可视化预览 |
| **TUI 透传** | vim、top、nano、htop —— 自然运行交互式程序 |
| **CWD 同步** | PTY 里的 `cd` 实时反映到 Onyx 的工作目录 |

全部基于 `prompt_toolkit`，底层是持久 PTY session。

---

## 🔌 PTY 引擎

启动时创建一个长生命周期的 PTY session（bash、zsh、fish 或 PowerShell），
所有命令 —— 你的和 AI 的 —— 都复用同一条 session。

两种执行模式共享同一个 PTY：

- **Passthrough** — raw TTY，实时 stdout 转发，stdin 透传。用于系统命令和 TUI 程序。
- **Wrapped** — 标记提取输出、CWD 读取、变量查询。用于需要结构化输出的 builtin 和工具。

这意味着任何来源的 `cd /tmp`（你、脚本、AI）都对后续所有命令生效。
一个终端，一条 session，一个共享的现实。

---

## 🔒 安全模型

安全检查在命令**到达 bash 之前**执行 —— 不是事后审计，不是 sandbox 封装。

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

### 黑名单与语法控制

100+ 条危险模式自动检测（`rm -rf /`、`mkfs`、fork 炸弹、`chmod 777`）。
管道、重定向、here-document、逻辑操作符可在细颗粒度路径中单独禁止 ——
防止通过 `cat /etc/shadow | nc` 之类的手法绕过。

---

## 🤖 AI Agent

`ai` 命令是一个 **builtin** —— 和 `cd`、`export`、`clear` 地位平等。
AI 通过 Onyx 的执行管线操作，而不是绕过终端运行时。

### 多模型支持

支持 DeepSeek、OpenAI、Ollama、Claude、Gemini、Grok、xAI、GitHub Copilot
及任何 OpenAI 兼容端点。SSE 流式传输，实时 Markdown 渲染。
模型列表维护在 `etc/ai/models.json` —— 编辑它即可增改模型，无需改代码。

### 执行管线

```
ai "找到占用 8080 端口的进程并杀掉"
  → SSE API（携带 OS、shell 类型、CWD、工具列表、模式、记忆上下文）
  → AI 返回结构化命令：["lsof -i :8080", "kill <PID>"]
  → 每条命令经过 parse_and_execute()
  → 和人类用户一样的安全检查 —— 没有任何东西绕过安全层
  → 同一个 PTY session → 状态是真实的、共享的
  → 输出捕获后返回给 AI 做下一步决策
```

AI 可以 `apt install` 然后立刻运行刚装好的程序。不需要预设工具封装。

### MCP 协议

内建 MCP（Model Context Protocol）支持 —— 连接 MCP 服务器获取文件系统、记忆等能力。
工具 schema 带指纹验证缓存。

---

## 🧠 认知记忆系统

Onyx 在多次 session 之间维持持久记忆，不只是聊天记录。

| 层级 | 路径 | 用途 |
|---|---|---|
| **最高指示** | `.ai_s/onyx_ai.md` | AI 通过 `[PROMPT]:` 自行维护的跨 session 持久指令 |
| **对话历史** | `.ai_s/chat/{name}.json` | 按 chat 分组的多轮对话，完整上下文保留 |
| **Session 记录** | `.ai_s/library/{id}.txt` | 每次 session 的完整输出、上下文与决策 |
| **情景记忆** | `.ai_s/memory/` | 从 session 中提取的关键事件和事实，带主动遗忘机制 |


AI 自主向 `.ai_s/onyx_ai.md` 写入跨重启的决策和偏好。情景记忆以 JSON 为核心结构，
自动淘汰旧条目或低重要性内容。

---

## 🏗️ 架构

每条命令都经过同一个管线：

```
universal_input()           prompt_toolkit 键盘捕获
        ↓
parse_and_execute()         分类：builtin / tool / system / unknown
        ↓
安全检查                    模式 + perm_path + dan_cmd + 语法控制
        ↓
run_cmd_sync()              持久 PTY → bash / zsh
        ↓
_sync_cwd_from_shell()      保持 Python CWD 与 PTY 同步
```

| 层 | 路径 | 职责 |
|---|---|---|
| 输入 | `lib/terminal/input_lib.py` | prompt_toolkit REPL、历史、多行、prompt 风格 |
| 补全 | `lib/terminal/com.py` | 幽灵补全、路径补全、语法高亮 |
| PTY 引擎 | `lib/terminal/exe.py` | PersistentShell — passthrough、CWD 同步、TTY raw 模式 |
| 调度 | `lib/parse_and_execute.py` | 分类 → 安全 → 执行 骨干 |
| 解析 | `lib/parse.py` | Shell 解析器（bash/zsh/fish/cmd/powershell） |
| 安全 | `lib/safe.py` | 模式、perm_path.json、dan_cmd、细颗粒控制 |
| AI Agent | `bin/ai_cmd.py` | SSE 流式、MCP、记忆、多模型 |
| 配置 | `etc/` | config.json、perm_path.json、dan_cmd、ai/models.json |
| C 扩展 | `lib/c/` | ARM64 C 模块（路径解析、缓存、扫描） |

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

## 📄 许可

MIT — 详见 [LICENSE](LICENSE)
