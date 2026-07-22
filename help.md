# Onyx-Native Help / Onyx-Native 帮助文档

Onyx is a terminal emulator + shell + security sandbox written in Python. It implements syntax
highlighting, ghost completion, security interception, and AI builtin commands at the input layer,
with the underlying PTY connected to real bash/zsh.

> Onyx 是一个用 Python 编写的终端模拟器 + Shell + 安全沙箱。它在输入层实现语法高亮、幽灵补全、安全拦截和 AI 内建命令，底层通过 PTY 连接真实的 bash/zsh。

## Architecture / 架构

```
User keyboard input → Onyx (Input + Parse + Security + AI) → PTY → bash/zsh → Kernel
用户键盘输入 → Onyx（输入 + 解析 + 安全 + AI）→ PTY → bash/zsh → 内核
```

## Quick Start / 快速开始

```bash
pip install prompt_toolkit colorama rich requests msgpack

python3 Onyx.py          # Start terminal / 启动终端
python3 Main.py -l       # Login mode / 登录模式
python3 cmd.py "ls -la"  # Single execution / 单次执行
python3 man.py           # Rebuild man index / 重建 man 命令索引
```

## Builtin Commands / 内建命令

| Command / 命令 | Description / 说明 |
|------|------|
| `cd <path>` | Change working directory (persists for subsequent commands) / 切换工作目录（变化对后续命令持久生效） |
| `clear` | Clear screen / 清屏 |
| `exit` | Exit Onyx / 退出 Onyx |
| `run <script>` | Run a script / 运行脚本 |
| `refresh` | Refresh tool index / 刷新工具索引 |
| `export <VAR>=<value>` | Set environment variable / 设置环境变量 |
| `activite` | Activate/switch security mode (low/mid/adv) / 激活/切换安全模式 |
| `manage <subcommand>` | Manage config (set/get config items) / 管理配置 |
| `import <file>` | Import config file / 导入配置文件 |
| `switch-prompt <style>` | Switch prompt style (kali/ubuntu/zsh/onyx/termux/def/skali) / 切换提示符风格 |
| `ai <prompt>` | AI assistant (requires backend service) / AI 助手（需后端服务） |
| `set-adv-pwd` | Set adv mode password / 设置 adv 模式密码 |
| `autocmd <on/off>` | Toggle auto-command / 自动命令开关 |
| `help` | Show this help / 显示此帮助 |
| `mktool` | Create new tool template / 创建新工具模板 |
| `unalias <name>` | Remove alias / 删除别名 |
| `source <file>` | Load config file / 加载配置文件 |
| `which <cmd>` | Find command location / 查找命令位置 |
| `sado <cmd>` | Run command with elevated permissions (sudo-like, via Onyx permission system) / 以高级权限运行命令 |
| `nanosado` | Lightweight privilege escalation / 轻量权限提升 |
| `alias <name>=<cmd>` | Create command alias (persists across restarts) / 创建命令别名（重启后依然有效） |
| `history` | Show command history / 查看命令历史 |
| `alias` | List all defined aliases / 列出所有定义的别名 |

## Sub-commands / 子命令详情

### `manage` — Config Manager / 配置管理

| Sub-command / 子命令 | Description / 说明 |
|------|------|
| `manage set <key> <val>` | Set a config item / 设置配置项 |
| `manage get <key>` | Get a config item / 获取配置项值 |
| `manage set language english` | Switch to English UI / 切换为英文界面 |
| `manage set language chinese` | Switch to Chinese UI / 切换为中文界面 |
| `manage set debug-times true` | Enable command execution timing / 开启命令耗时显示 |
| `manage set debug-parsecmd true` | Enable command parsing debug output / 启用命令解析调试输出 |
| `manage set clean-log-time <days>` | Set log auto-clean interval / 设置日志自动清理天数 |

### `activite` — Security Mode / 安全模式切换

| Flag / 参数 | Description / 说明 |
|------|------|
| `activite -m low` | Strict mode — whitelist only / 严格模式，仅允许白名单命令 |
| `activite -m mid` | Moderate mode — relaxed permission / 宽松拦截 |
| `activite -m adv` | Advanced mode — dialog confirm + argon2id password / 弹框确认 + 密码验证 |

### `switch-prompt` — Prompt Styles / 提示符风格

| Style / 风格 | Description / 说明 |
|------|------|
| `switch-prompt kali` | Kali Linux style prompt / Kali Linux 风格 |
| `switch-prompt ubuntu` | Ubuntu style prompt / Ubuntu 风格 |
| `switch-prompt zsh` | Z shell style prompt / Zsh 风格 |
| `switch-prompt onyx` | Onyx default style / Onyx 默认风格 |
| `switch-prompt termux` | Termux style prompt / Termux 风格 |
| `switch-prompt def` | Default minimalist prompt / 默认简约风格 |
| `switch-prompt skali` | Simplified Kali prompt / 简化版 Kali 风格 |

## MCP (Model Context Protocol) / MCP 集成

Onyx supports MCP for AI tool integration:
- `etc/mcp/mcp.json` — MCP server configuration / MCP 服务器配置
- `bin/ai_lib/mcp_client.py` — MCP client implementation / MCP 客户端实现
- `bin/ai_lib/mcp_registry.py` — Tool registry for MCP tools / MCP 工具注册
- `bin/ai_lib/mcp_transport.py` — Transport layer (SSE/stdio) / 传输层实现

> MCP 允许 AI 通过标准协议调用外部工具，扩展 AI 能力边界。

## Task System / 任务系统

Onyx includes a built-in task system (`lib/task_system/`):
- **Task Registry** — Register and manage tasks / 任务注册与管理
- **Cron Registry** — Scheduled task execution / 定时任务调度
- **Team Registry** — Team-based task coordination / 团队协作任务
- **Task Packet** — Structured task definitions / 结构化任务定义

## TBS vs OS Mode / TBS 与 OS 模式

| Mode / 模式 | Behavior / 行为 |
|------|------|
| **TBS** | Virtual sandbox — all operations stay within the virtual root. Real system untouched. / 虚拟沙箱，所有操作在虚拟根目录内，不影响真实系统 |
| **OS** | Full system access — virtual root = system root. Full read/write. / 完整系统访问，虚拟根目录 = 系统根目录 |

Onyx auto-detects which mode to use. When `ROOT_DIR` equals the system root (`/` or `C:\`), OS mode is activated.

## Security Modes / 安全模式

| Mode / 模式 | Description / 说明 |
|------|------|
| `low` | Strict interception, whitelist-only commands / 严格拦截，仅允许白名单命令 |
| `mid` | More relaxed permission ceiling / 更宽松的权限上限 |
| `adv` | Dialog confirm + remember choice (requires argon2id password verification) / 弹框确认 + 可记住选择（需 argon2id 密码验证） |

## Path-level Fine-grained Permissions / 路径级细颗粒度权限

`etc/perm_path.json` defines path-level permission rules. The same `rm` command can execute under
`/tmp` but is blocked under `/etc` — something traditional filesystem permissions cannot achieve.

> `etc/perm_path.json` 定义路径级权限规则。同一个 `rm` 命令在 `/tmp` 下可以执行，在 `/etc` 下被拦截——这是传统文件系统权限做不到的。

Rule example / 规则示例：
```json
"/etc/<*:10>": {
  "mode": "whitelist",
  "allow_advanced_syntax": false,
  "commands": ["ls", "cd", "cat", "grep"]
}
```

## Dangerous Command Blacklist / 高危命令黑名单

`etc/dan_cmd` defines interception patterns, one per line. Includes `rm -rf /`, `mkfs`,
`dd if=/dev/zero`, and 100+ other rules.

> `etc/dan_cmd` 定义拦截模式，一行一个。包括 `rm -rf /`、`mkfs`、`dd if=/dev/zero` 等 100+ 条规则。

## AI Features / AI 功能

The `ai` command communicates with the backend via SSE API. The returned command list goes through
the entire parse-security-check-execute pipeline one by one. The AI's `cd /tmp` takes effect for the
next command (persistent PTY session).

> `ai` 命令通过 SSE API 与后端通信，返回的命令列表会逐个走完整个解析-安全检查-执行管线。AI 的 `cd /tmp` 对下一条命令生效（持久 PTY 会话）。

## Ghost Completion / 幽灵补全

Based on command frequency, as you type, the highest-probability suggestion appears in grey text
directly after your cursor — no Tab needed.

> 基于命令频率，在你输入的同时，最高概率的建议以灰色文本直接显示在光标后面——不需要按 Tab。

## Project Structure / 项目结构

```
Onyx.py              Terminal main program (REPL, all builtins) / 终端主程序
Main.py              Launcher (env check + cache) / 启动器
cmd.py               Single command executor / 单命令执行器
lib/terminal/exe.py  PTY persistent shell / PTY 持久 shell
lib/safe.py          Security module (perm_path.json, dan_cmd, three-mode confirmation) / 安全模块
lib/parse.py         Shell parser (bash/zsh/fish/cmd/powershell) / Shell 解析器
lib/parse_and_execute.py  Command dispatch backbone / 命令调度主干
bin/                 Builtin command implementations / builtin 命令实现
etc/                 Runtime config / 运行时配置
```

## License / 许可

GPL v3
