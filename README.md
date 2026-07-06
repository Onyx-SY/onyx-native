<p align="center">
  <img src="https://img.shields.io/badge/python-3.8+-blue" alt="Python">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  <img src="https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Termux-lightgrey" alt="Platform">
</p>

<h1 align="center">Onyx-native</h1>
<h3 align="center">Terminal Emulator · Shell · Security Sandbox · AI Builtin</h3>

<p align="center"><a href="https://onyxsy.com"><b>onyxsy.com</b></a></p>

---

## 🇬🇧 English

Onyx is a **terminal emulator frontend** written in Python. It merges the input layer, command
parsing, mandatory security policies, and AI builtins into a single process, with the underlying
PTY connected to a real bash/zsh shell.

```
Traditional:  Terminal Emulator  →  Shell (bash/fish/zsh)  →  Kernel
Onyx:         Onyx (Input + Parse + Security + AI)  →  PTY  →  bash/zsh  →  Kernel
```

### 🖥️ Why a Terminal Emulator

| Feature | Traditional Terminal | Onyx |
|---|---|---|
| Ghost Completion | ❌ Tab-based | ✅ Inline grey text as you type |
| Syntax Highlighting | ❌ After Enter | ✅ Real-time in input area |
| Multi-line Context | ❌ Manual | ✅ Auto-detects `if` → `then` → `fi` |
| Prompt Styles | 1 | 8 (kali, ubuntu, zsh, onyx, …) |
| Login Mode | Shell-level | ✅ Terminal-level (`-l` flag) |

### 🔒 Security Pipeline

Commands pass through security **before** reaching the PTY — not post-audit:

| Layer | Config | Description |
|---|---|---|
| 3 Modes | low / mid / adv | Strict block → Permission ceiling → Dialog confirm |
| Path Permissions | `etc/perm_path.json` | `rm` allowed in `/tmp`, blocked in `/etc` |
| Blacklist | `etc/dan_cmd` | 100+ patterns: `rm -rf /`, `mkfs`, `dd if=/dev/zero` |
| Syntax Control | pipes, redirects, here-docs | Forbidden in fine-grained paths |

### 🤖 AI as a Builtin

`ai` has the same standing as `cd`, `export`, or `clear`. It calls a remote SSE API, then feeds
returned commands through the **same** parse → security → execute pipeline.

```
ai "kill process on port 8080"
  → SSE API (OS, shell, CWD, tools, memory, mode)
  → [{ commands: ["lsof -i :8080", "kill <PID>"], class: "3" }]
  → Each command: parse_and_execute() → security check → PTY execution
```

The AI actually **uses the computer** — not just calls tools:
- `cd /tmp` persists for the next command (same PTY session)
- `apt install nmap && nmap localhost` — no preset wrappers needed
- Controls memory retention via `class` field (1-5)

### 🧠 Memory & Sessions

| Type | Path | Description |
|---|---|---|
| Chat history | `.ai_s/chat/{name}.json` | Multi-turn dialog grouped by chat |
| Session records | `.ai_s/library/{id}.txt` | Per-session output & context |
| Current chat | `.ai_s/chat.txt` | Active chat name pointer |

### 🚀 Quick Start

```bash
pip install prompt_toolkit colorama rich requests msgpack
python3 Onyx.py          # Start
python3 Main.py -l       # Login mode
python3 cmd.py "ls -la"  # Single command
```

### 📁 Project Structure

| Path | Description |
|---|---|
| `Onyx.py` | Main program (boot, REPL, builtins) |
| `lib/terminal/exe.py` | PTY persistent shell |
| `lib/terminal/input_lib.py` | Input handling, history, multi-line |
| `lib/terminal/com.py` | Completion + syntax highlighting |
| `lib/parse_and_execute.py` | Command dispatch backbone |
| `lib/safe.py` | Security module |
| `lib/parse.py` | Shell parser (bash/zsh/fish/cmd/powershell) |
| `bin/ai_cmd.py` | AI builtin |
| `bin/` | Other builtins |
| `etc/` | Runtime config |
| `lib/c/` | ARM64 C extensions |

### 📄 License

MIT — see [LICENSE](LICENSE)

---

---

## 🇨🇳 中文

Onyx 是一个用 Python 编写的**终端模拟器前端**，它将输入层、命令解析、强制安全策略和 AI builtin
合并到一个进程里，底层通过 PTY 连接到真实的 bash/zsh。

```
传统架构：  终端模拟器  →  Shell (bash/fish/zsh)  →  内核
Onyx：      Onyx（输入 + 解析 + 安全 + AI）  →  PTY  →  bash/zsh  →  内核
```

### 🖥️ 为什么是终端模拟器

| 特性 | 传统终端 | Onyx |
|---|---|---|
| 幽灵补全 | ❌ 按 Tab | ✅ 输入时灰色文字直接显示在光标后 |
| 语法高亮 | ❌ 回车后 | ✅ 输入区实时着色 |
| 多行上下文 | ❌ 手动 | ✅ 输入 `if` 自动感知 `then`/`fi` |
| Prompt 风格 | 1 种 | 8 种（kali / ubuntu / zsh / onyx …） |
| 登录模式 | Shell 级 | ✅ 终端级（`-l` 参数） |

### 🔒 安全管线

命令在**到达 PTY 之前**先过安全检查，不是事后审计：

| 层级 | 配置 | 说明 |
|---|---|---|
| 三级模式 | low / mid / adv | 严格拦截 → 权限上限 → 弹框确认 |
| 路径级权限 | `etc/perm_path.json` | 同一个 `rm` 在 `/tmp` 可执行，在 `/etc` 被拦截 |
| 黑名单 | `etc/dan_cmd` | 100+ 条规则：`rm -rf /`、`mkfs`、`dd if=/dev/zero` |
| 语法控制 | 管道、重定向、here-doc | 在细颗粒度路径中可被禁止 |

### 🤖 AI 是一个 Builtin

`ai` 命令和 `cd`、`export`、`clear` 地位完全平等，唯一的区别是它调用远程 SSE API，
然后把返回的命令列表逐个喂回**同一个**解析 → 安全检查 → 执行管线。

```
ai "干掉占用 8080 端口的进程"
  → SSE API（携带 OS、shell、CWD、工具列表、当前模式、记忆）
  → [{ commands: ["lsof -i :8080", "kill <PID>"], class: "3" }]
  → 每条命令：parse_and_execute() → 安全检查 → PTY 执行
```

AI 不是「调用工具」，而是**真正使用电脑**：
- `cd /tmp` 对下一条命令生效（同一个 PTY session）
- `apt install nmap && nmap localhost` — 不需要预设工具封装
- 通过 `class` 字段控制记忆保留级别（1-5）

### 🧠 记忆与 Session

| 类型 | 路径 | 说明 |
|---|---|---|
| 对话历史 | `.ai_s/chat/{name}.json` | 按 chat 分组的多轮对话 |
| Session 记录 | `.ai_s/library/{id}.txt` | 每次 session 的输出与上下文 |
| 当前 Chat | `.ai_s/chat.txt` | 当前活跃的 chat 名称 |

### 🚀 快速开始

```bash
pip install prompt_toolkit colorama rich requests msgpack
python3 Onyx.py          # 启动
python3 Main.py -l       # 登录模式
python3 cmd.py "ls -la"  # 单次执行
```

### 📁 项目结构

| 路径 | 说明 |
|---|---|
| `Onyx.py` | 主程序（启动、REPL、builtin） |
| `lib/terminal/exe.py` | PTY 持久 Shell |
| `lib/terminal/input_lib.py` | 输入处理、历史、多行 |
| `lib/terminal/com.py` | 补全 + 语法高亮 |
| `lib/parse_and_execute.py` | 命令调度主干 |
| `lib/safe.py` | 安全模块 |
| `lib/parse.py` | Shell 解析器（bash/zsh/fish/cmd/powershell） |
| `bin/ai_cmd.py` | AI 内置 |
| `bin/` | 其余 builtin |
| `etc/` | 运行时配置 |
| `lib/c/` | ARM64 C 扩展 |

### 📄 许可

MIT — 详见 [LICENSE](LICENSE)
