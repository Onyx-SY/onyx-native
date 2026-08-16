# Onyx — 终端介绍（Introduction）

> 本文件为 Onyx 终端与 AI 助手的详细介绍，供 AI 在需要时通过 `memory list skill=onyx` 查看。不随系统前缀加载，按需查询，省 token。

## 是什么

**Onyx** 是一个交互式 AI 终端：内置 AI 助手 + 完整 shell 能力。AI 可以直接读写文件、执行命令、联网调研、派生子代理，以函数调用（function calling）方式使用一套只读/写/危险分级权限的工具集。

## 架构概览

- **`Main.py` / `Onyx.py`** — 入口与终端主循环。
- **`bin/`** — AI 命令体系：
  - `ai_cmd.py` — 主 AI 循环（handle_ai）：系统提示拼接、工具分发、记忆注入、成本统计。
  - `ai_interactive.py` — AI 对话模式（REPL）：`/` 斜杠命令、多轮对话历史、记忆模式。
  - `ai_lib/` — 核心库：`api.py`（SSE 流式调用）、`storage.py`（会话/记忆存储）、`memory_tools.py`（记忆工具执行器）、`timeline.py`（时间线）、`subagent.py`（子代理）、`plus.py`（高级思考流水线）、`config.py`（模型注册表）、`cost.py`（成本/模型选择）、`prompt_cache.py`（前缀缓存）、`parsers.py`、`i18n.py`、`lang.json`。
- **`core/`** — 终端核心：命令解析、沙箱、工具注册、内置命令处理器。
- **`etc/ai/`** — AI 配置：`agreement.md`（加载入口）、`self.md`（自我认知）、`skill.md`（技能）、`onyx.md`（本文件）、`models.json`（模型注册表）、`mood.md`。
- **`.onyx/skills/`** — 技能文档目录（debug/refactor/task-workflow/builtin-commands 等），可用 `memory list skill=<name>` 读取。

## 记忆系统

记忆根 `~/.ai_s/`：
- `library/<uuid>.txt` — 原始会话记录（每条任务一个 UUID）。
- `time/YYYY/MM/YYYY-M-D/list.json` — 每日任务索引（与旧 chat.json 同构）。
- `timeline.json` — 分层摘要：day（100字）/ month / year，跨边界后首次查询时用最便宜模型惰性生成。
- `chat/` — 旧版平铺索引（兼容保留）。

记忆工具：`MemoryRead`（按 UUID 读会话）、`MemorySearch`（关键词全文搜索）、`memory`（search/list/read + 时间线 day/month/year/start/end + skill 文档查询）、`remember`/`forget`（保留/归档）、`compact_stats`（压缩状态）。

## AI 对话模式斜杠命令

- `/plus` — 一次性高级模式：任务前先跑「分析→模拟→自检→规划」4 步思考（当前系列最贵模型），思考结果注入干活阶段；只对下一次任务有效。
- `/mode` — 切换 AI 模式（normal/plan）。
- `/model` — 查看/切换 AI 模型与参数。
- `/memory` — 切换记忆模式（global/project）。
- `/lang` — 切换中/英。
- `/key` — 配置 API 密钥。
- `/tokens` `/cost` — token/成本统计。
- `/help` `/exit` `/quit` `/clear` `/quiet` — 帮助/退出/清屏/精简模式。

## 内置命令（终端层）

- `manage set <key> <value>` — 设置配置（language / mcp / debug-times / clean-log-time / assistant）；`manage clean <cache|logs|all>` 清理。
- `activite -m <low/mid/adv>` — 切换活跃模式；adv 需要密码（AI 直接生成命令，不询问用户）。
- `sado <command>` — 提权执行（必须位于命令行最前面）。
- `switch-prompt <list|preview <theme>|switch <theme>>` — 主题。
- `autocmd add <cmd>` — 自动命令；`mktool -n <name> -l <lang>` — 创建工具；`tml install <name>` — 安装 tml 包。
- 内置命令不支持 bash 语法（由 Onyx 解析），`RunCommand` 不受此限。

## 工具体系

- **ReadOnly**（自动执行）：read_file / grep_search / glob_search / DirectoryTree / Lsp* / py_* / Memory* / web_search 等。
- **WorkspaceWrite**（轻确认）：write_file / edit_file / validate_edit / preview_edit / TodoWrite 等。
- **DangerFullAccess**（显式确认）：RunCommand 危险命令、删除/移动/复制、高影响操作。
- 子代理：`Agent` 工具，explore/plan 自动执行，lint/test/web_search_agent 需显式批准，最多 5 个并发。

## 模型与成本

- `etc/ai/models.json` 注册多平台模型（deepseek / openai / anthropic / custom），含单价表（USD / 1M tokens）。
- 模型选择策略：主 AI 用默认模型；子代理 explore 用最便宜（flash 档）；plan 自动升档（flash → pro）；plus 思考用最贵（pro 档）。
- DeepSeek 支持前缀缓存（system prompt 静态部分命中缓存，省成本）。

## 使用建议

- 简单问题直接答；正式任务先 `Agent(type="plan")` 规划再执行；任务用 `TodoWrite` 跟踪。
- 记忆按需查询：回顾昨天用 `memory list day=昨天`；本周总结用区间查询；技能文档用 `memory list skill=<name>`。
