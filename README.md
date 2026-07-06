# Onyx-native — Terminal Emulator + Shell + Security Sandbox

**[onyxsy.com](https://onyxsy.com)**

Onyx is a **terminal emulator frontend** written in Python. It merges the terminal input layer, shell
command parsing, mandatory security policies, and AI builtins — all running in a single process,
with the underlying PTY connected to real bash/zsh.

> # Onyx-native — 终端模拟器 + Shell + 安全沙箱
>
> Onyx 是一个用 Python 写的**终端模拟器前端**，它合并了终端输入层、shell 命令解析、强制安全策略、以及 AI builtin
> ——所有这些运行在同一个进程里，底层通过 PTY 连接到真实的 bash/zsh。

In a traditional architecture, your terminal emulator (Kitty / iTerm2 / Windows Terminal) handles
input rendering, the inner shell (bash / fish / zsh) handles command execution, and security relies
on sudo and file permissions. Onyx combines all three into one.

> 传统架构里你的终端模拟器（Kitty / iTerm2 / Windows Terminal）管输入渲染，里面的 shell
> （bash / fish / zsh）管命令执行，安全靠 sudo 和文件权限。Onyx 把三件事合到了一起。

```
Traditional architecture:
  Terminal Emulator → Shell (bash/fish/zsh) → Kernel

Onyx architecture:
  Onyx (Input + Parse + Security + AI) → PTY → bash/zsh → Kernel

传统架构：
  终端模拟器  →  shell (bash/fish/zsh)  →  内核

Onyx 架构：
  Onyx（输入 + 解析 + 安全 + AI） →  PTY  →  bash/zsh  →  内核
```

## Why this is a terminal emulator, not just a shell

**A shell runs inside a terminal; Onyx IS the terminal.**

- **IDE-level interaction**: fish's completion is an industry benchmark, but it runs inside your
  terminal emulator — it doesn't know what you're typing until you press Tab. Onyx uses
  prompt_toolkit to directly capture keyboard input — every keystroke you make, Onyx is already
  doing real-time syntax highlighting, ghost completion, and multi-line context awareness.
- **Ghost Completion**: Based on command frequency, as you type, the highest-probability suggestion
  appears in grey text directly after your cursor. No Tab needed, no waiting.
- **Multi-line code input**: The moment you type `if`, Onyx knows `then` and `fi` should follow —
  completion and indentation adapt to context automatically. bash's multi-line experience isn't even
  in the same era.
- **`-l` login mode**: `python3 Main.py -l` simulates a login shell environment — a terminal-level
  feature, not a shell-level one.
- **8 prompt styles**: kali, ubuntu, zsh, onyx, and more, switchable via the `switch-prompt` command.
- **Syntax highlighting**: Your command is real-time colored in the input area BEFORE you press
  Enter. Which shell does this? None. They all rely on the terminal emulator for this capability.
  Onyx IS the terminal emulator.

> ## 为什么这是一个终端模拟器，而不只是 shell
>
> **shell 运行在终端里面，Onyx 就是终端。**
>
> - **IDE 级交互**：fish 的补全是行业标杆，但它运行在你的终端模拟器里，按下 Tab 之前它不知道你在打字。
>   Onyx 用 prompt_toolkit 直接接管键盘输入 —— 你每按一个键，Onyx 都已经在实时做语法高亮、幽灵补全、多行上下文感知。
> - **幽灵补全（Ghost Completion）**：基于命令频率，在你输入的同时，最高概率的建议以灰色文本直接显示在光标后面。
>   不需要按 Tab，不需要等待。
> - **多行代码输入**：输入 `if` 的瞬间，Onyx 知道后面应该有 `then`、`fi`，补全和缩进自动适配上下文。
> - **`-l` 登录模式**：`python3 Main.py -l` 模拟 login shell 环境，这是终端级特性，不是 shell 级。
> - **8 种 prompt 风格**：kali、ubuntu、zsh、onyx 等可切换，通过 `switch-prompt` 命令实时更换。
> - **语法高亮**：你的命令在回车之前，已经在输入区被实时着色了。

## Security is welded into the input layer

Onyx doesn't do "post-audit". When you press Enter in the input area, the command first passes
through a complete security check pipeline — only then does it reach the PTY:

- **Three-tier mode**: low (strict block) / mid (higher permission ceiling) / adv (dialog confirm + remember choice)
- **Path-level fine-grained permissions**: `etc/perm_path.json` defines rules. Example: under
  `/home/user`, `ls` and `cat` are allowed, `rm` and `mv` are forbidden; the same `rm` can execute
  under `/tmp`. The same command has different permissions under different paths — this is something
  file permissions cannot achieve.
- **Dangerous command blacklist**: `etc/dan_cmd` defines interception patterns, one per line.
  `rm -rf /`, `mkfs`, `dd if=/dev/zero`, and 100+ other patterns are auto-detected.
- **Advanced syntax control**: pipes, redirects, here-docs, and logical operators can be forbidden
  in fine-grained paths, preventing bypass.

> ## 安全是焊在输入层的
>
> Onyx 不做「事后审计」。一条命令在输入区回车的那一刻，先经过完整的安全检查，通过之后才到 PTY：
>
> - **三级模式**：low（严格拦截）/ mid（更高权限上限）/ adv（弹框确认 + 可记住选择）
> - **路径级细颗粒度权限**：`etc/perm_path.json` 定义规则。同一个 `rm` 命令在 `/tmp` 下可以执行，在 `/etc` 下被拦截。
> - **高危命令黑名单**：`etc/dan_cmd` 定义拦截模式，包括 `rm -rf /`、`mkfs` 等 100+ 条规则。
> - **高级语法控制**：管道、重定向、here-doc、逻辑操作符在细颗粒度路径中可被禁止。

## AI is a builtin, not the center of the architecture

The `ai` command has exactly equal standing with `cd`, `history`, `clear`, `export` — all are
builtins registered in the command handler. The only difference: `ai` calls a remote SSE API, then
feeds the returned command list one by one through the **same** parse-security-check-execute pipeline.

```
ai "find and kill the process occupying port 8080"
  → Call SSE API (carrying env context: OS, shell, CWD, tool list, current mode, memory)
  → Server returns { commands: ["lsof -i :8080", "kill <PID>"], answer: "yes", txt: "...", class: "3" }
  → Each command goes through the same parse_and_execute() → same security check → same PTY execution
  → Output captured and recorded to session file (~/.onyx/library/{id}.txt)
  → class=3 controls memory retention time
```

This is why Onyx's AI isn't "calling tools" but "using the computer":
- AI's `cd /tmp` takes effect for the next command (persistent PTY session, not subprocess)
- AI can `apt install nmap` then immediately `nmap localhost` — no preset tool wrappers needed
- AI can ask the user back (the `ask` field), wait before continuing (the `sleep` field),
  directly edit files (the `path` + `edit_prompt` fields), and specify memory retention level
  (the `class` field)

> ## AI 是一个 builtin，不是架构的中心
>
> `ai` 命令和 `cd`、`history`、`clear`、`export` 地位完全平等，都是注册在命令处理器里的 builtin。
> 唯一的区别是：`ai` 会调远程 SSE API，然后把返回的命令列表逐个喂回**同一个**解析-安全检查-执行管线。

## Three-tier memory system

| Tier | Storage | Description |
|------|---------|-------------|
| Working memory | In-memory | Current session's dialog and command context |
| Episodic memory | `~/.onyx/chats/{name}.json` | Multi-turn dialogs grouped by chat |
| Autobiographical memory | `~/.onyx/library/{id}.txt` | Complete operation records, class 1-5 controls the forgetting curve |

> ## 三级记忆系统
>
> | 层级 | 存储 | 说明 |
> |---|---|---|
> | 工作记忆 | 内存 | 当前 session 的对话和命令上下文 |
> | 情景记忆 | `~/.onyx/chats/{name}.json` | 按 chat 分组的多轮对话 |
> | 自传体记忆 | `~/.onyx/library/{id}.txt` | 完整的操作记录，class 1-5 控制遗忘曲线 |

AI can actively reference any historical session by returning the `memory` field (UUID).

> AI 可以通过返回 `memory` 字段（UUID）主动引用任意历史 session。

## Quick Start / 快速开始

```bash
pip install prompt_toolkit colorama rich requests msgpack

python3 Onyx.py          # Start terminal / 启动终端
python3 Main.py -l       # Login mode / 登录模式
python3 cmd.py "ls -la"  # Single execution / 单次执行
python3 man.py           # Rebuild man index / 重建 man 命令索引
```

## Project Structure / 项目结构

```
Onyx.py                   Terminal main program (35-step boot, REPL, all builtins)
                          / 终端主程序（35 步启动、REPL、全部 builtin）
Main.py                   Launcher (env check + cache) / 启动器（环境检查 + 缓存）
cmd.py                    Single command executor / 单命令执行器
man.py                    man-page scanner / man-page 扫描器
lib/terminal/exe.py       PTY persistent shell (PersistentShell, marker extraction, CWD sync)
                          / PTY 持久 shell
lib/terminal/input_lib.py Input handling (universal_input, history, multi-line)
                          / 输入处理
lib/terminal/com.py       Completion + highlighting (ghost completion, path completion, ptk.json config)
                          / 补全 + 高亮
lib/parse.py              Shell parser (5 terminal types: bash/zsh/fish/cmd/powershell)
                          / Shell 解析器
lib/parse_and_execute.py  Command dispatch backbone (parse → classify → security → unified execution)
                          / 命令调度主干
lib/safe.py               Security module (perm_path.json, dan_cmd, three-mode confirmation)
                          / 安全模块
bin/ai_cmd.py             AI builtin (SSE API, memory system, auto-execution)
                          / AI builtin
bin/                      Other builtins (manage, run, sado, activite, export, …)
                          / 其余 builtin
etc/                      Runtime config (config.json, perm_path.json, dan_cmd, …)
                          / 运行时配置
lib/c/                    ARM64 C extensions / ARM64 C 扩展
```

## License / 许可

MIT License — see [LICENSE](LICENSE)
