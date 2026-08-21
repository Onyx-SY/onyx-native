# -*- coding: utf-8 -*-
"""
native_tools.py — 原生工具表构建（OpenAI function calling schema）

从 bin/ai_cmd.py 拆分（模块化架构重构）：
- _make_tool / build_native_tools / build_mcp_tools_prompt / 工具表冻结缓存；
- 权限级别常量（ReadOnly / WorkspaceWrite / DangerFullAccess）；
- 对 ai_cmd 的少量引用（_mcp_debug 系列、get_mcp_tools）在函数体内延迟导入，
  避免模块级循环导入。
"""

from typing import Any, Dict, List, Optional, Tuple

from .config import USER_HOME_DIR, get_current_lang
from .i18n import _ as _i18n
from .tools import code_analysis


def build_mcp_tools_prompt(lang: str = "chinese", user_home_dir: str = None) -> str:
    """
    构建注入给 AI 的工具说明提示词。
    文件操作已由原生标记语言覆盖，这里只展示非文件类 MCP 工具。
    """
    # 延迟导入（避免循环依赖：ai_cmd 在模块级导入本模块）
    from ..ai_cmd import _mcp_debug_enter, _mcp_debug_exit, _mcp_debug
    from ..ai_cmd import get_mcp_tools
    _mcp_debug_enter("build_mcp_tools_prompt")
    tools = get_mcp_tools(user_home_dir=user_home_dir)

    # ── 过滤掉 filesystem 工具（文件操作用原生标记语言）──
    non_file_tools = []
    for t in tools:
        name = t.get("name", "")
        # filesystem 工具的常见名
        if name in ("read_file", "write_file", "edit_file",
                     "create_directory", "list_directory",
                     "directory_tree", "move_file", "copy_file",
                     "delete_file", "delete_directory",
                     "get_file_info", "search_files", "search_content",
                     "glob", "find_on_path", "get_workspace_folders"):
            continue
        non_file_tools.append(t)

    _mcp_debug(f"get_mcp_tools 返回 {len(tools)} 个工具，过滤后 {len(non_file_tools)} 个")

    if not non_file_tools:
        # 没有非文件 MCP 工具，返回空字符串（不占用 prompt 空间）
        _mcp_debug_exit("build_mcp_tools_prompt", ok=True, detail="only file tools, skipped")
        return ""

    lines = []
    lines.append("## Non-file Tools (Function Calling)")
    lines.append("All tools use standard function calling (tool_calls) — call them directly through the API, never in text.")
    lines.append("The tools are already in your API function calling list — call them directly.")

    lines.append("")

    for tool in non_file_tools:
        raw_name = tool.get("name", "?")
        full_name = raw_name  # 不再加 mcp__filesystem__ 前缀
        desc = tool.get("description", "")
        schema = tool.get("inputSchema", {})
        props = schema.get("properties", {})
        required = schema.get("required", [])

        # 构建 JSON 参数说明
        param_entries = []
        for pname, pinfo in props.items():
            req_mark = " (required)" if pname in required else ""
            ptype = pinfo.get("type", "string")
            pdesc = pinfo.get("description", "")
            param_entries.append(f'    "{pname}": {{{{ {ptype} }}}}{req_mark} — {pdesc}')

        lines.append(f"- **{full_name}**: {desc}")
        if param_entries:
            lines.append("  params:")
            lines.extend(param_entries)

        lines.append("")

    result = "\n".join(lines)
    _mcp_debug_exit("build_mcp_tools_prompt", ok=len(tools) > 0, detail=f"{len(tools)} tools, {len(result)} chars")
    return result


def build_native_tools_prompt() -> str:
    """Build AI tool guide — pure English, function calling only."""
    lines = []
    lines.append("## File Operations (Function Calling)")
    lines.append("Use standard function calling tools for file read/write/edit.")
    lines.append("")
    lines.append("### Available Tools")
    lines.append("- `get_file_info(path)` — Get file info (size/lines/mtime)")
    lines.append("- `read_file(path, range?)` — Read file, range='10-30' for line range")
    lines.append("- `edit_file(path, old_string, new_string)` — SEARCH/REPLACE edit")
    lines.append("- `write_file(path, content)` — Create/overwrite file")
    lines.append("- `validate_edit(file_path, search, replace)` — Validate SEARCH exists and unique")
    lines.append("- `preview_edit(file_path, search, replace)` — Preview diff")
    lines.append("")
    lines.append("### Guidelines")
    lines.append("1. **Check first**: Call `get_file_info` then `read_file` before editing")
    lines.append("2. **Prefer edit_file**: Local changes → `edit_file`; new file or >70% change → `write_file`")
    lines.append("3. **Large file chunking — MUST**: Files >20KB: create a skeleton with `write_file`, then fill with multiple `edit_file` chunks (<200 lines each). NEVER write the full content of a >20KB file in one `write_file` — it truncates and corrupts. Read back to verify.")
    lines.append("4. **Validate before edit**: Always call `validate_edit` before `edit_file`")
    lines.append("5. **Unique anchor**: `edit_file` old_string must be byte-exact and unique")
    lines.append("6. **Shell**: use `RunCommand(command)` tool for shell commands — output is captured and returned")
    lines.append("")
    lines.append("### Planning Tools")
    lines.append("- `submit_plan(plan, steps?)` — Submit plan for user approval; steps can be structured")
    lines.append("- `mark_step_complete(step_id)` — Mark one step done after completion")
    lines.append("- `TodoWrite(todos)` — Track in-session task list for multi-step work")
    lines.append("")
    lines.append("### Communication Tools")
    lines.append("- `choose_ask(question, options)` — Present options to user when uncertain")
    lines.append("- `Skill(name, args?)` — Load a reusable skill playbook (e.g. debug, task-workflow, refactor)")
    lines.append("")
    lines.append("> Reply in plain Markdown only — your text is displayed to the user as-is. No wrappers, no special formats: just speak naturally in Markdown.")
    return "\n".join(lines)


# ── 权限级别常量 ──
PERM_READONLY = "ReadOnly"           # 安全只读，自动放行
PERM_WORKSPACE_WRITE = "WorkspaceWrite"  # 修改工作区，需轻确认
PERM_DANGER_FULL = "DangerFullAccess"    # 危险操作，需显式批准


def _make_tool(name: str, description: str, properties: dict, required: list,
               permission: str = PERM_READONLY) -> Dict:
    """构建标准 OpenAI function calling 工具定义。

    描述自动本地化：优先从 i18n 模块读取 tool_desc.<name> /
    tool_p.<name>.<param>（跟随当前 UI 语言），未收录时回退到代码内嵌的默认文本。
    单语言而非双语——双语拼接让每条描述体积翻倍（AI 侧信息完全重复）。
    """
    _tool_lang = get_current_lang()  # "chinese" / "english"，跟随 /lang 切换
    _desc = _i18n(f"tool_desc.{name}", _tool_lang)
    if _desc == f"tool_desc.{name}":
        _desc = description
    # ── 权限文案自动生成：描述与强制层保持一致，防止“承诺了但没强制”──
    # 仅当使用代码内嵌描述（无 i18n 覆盖）时追加权限声明。
    if _desc == description and permission in (PERM_WORKSPACE_WRITE, PERM_DANGER_FULL):
        _perm_hint = {
            PERM_WORKSPACE_WRITE: (
                "（写入工作区：自动放行，可用 UndoLastEdit 撤销）"
                if _tool_lang == "chinese"
                else " (workspace write: auto-approved, reversible via UndoLastEdit)"
            ),
            PERM_DANGER_FULL: (
                "（危险操作：需用户显式批准，low/mid 模式弹确认）"
                if _tool_lang == "chinese"
                else " (dangerous access: requires explicit user approval in low/mid mode)"
            ),
        }.get(permission, "")
        if _perm_hint:
            _desc = _desc.rstrip() + _perm_hint
    _props = {}
    for _pkey, _pval in properties.items():
        _pval = dict(_pval)
        _pdesc = _i18n(f"tool_p.{name}.{_pkey}", _tool_lang)
        if _pdesc != f"tool_p.{name}.{_pkey}":
            _pval["description"] = _pdesc
        # 嵌套参数（array items 的 properties）同样本地化
        _items = _pval.get("items")
        if isinstance(_items, dict):
            _items = dict(_items)
            _sub_props = _items.get("properties")
            if isinstance(_sub_props, dict):
                for _skey, _sval in list(_sub_props.items()):
                    _sdesc = _i18n(f"tool_p.{name}.{_pkey}.{_skey}", _tool_lang)
                    if _sdesc != f"tool_p.{name}.{_pkey}.{_skey}":
                        _sval = dict(_sval)
                        _sval["description"] = _sdesc
                        _sub_props[_skey] = _sval
            _pval["items"] = _items
        _props[_pkey] = _pval
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": _desc,
            "parameters": {
                "type": "object",
                "properties": _props,
                "required": required,
                "additionalProperties": False,
            },
        },
        "x_permission": permission,  # 自定义字段，用于执行时权限检查
    }


def build_native_tools(user_home_dir: str = None) -> List[Dict]:
    """Build OpenAI-compatible tools array — full Onyx native tool set.

    Permission levels: ReadOnly (auto), WorkspaceWrite (light confirm), DangerFullAccess (approval).
    Each tool has exact JSON Schema parameters (type, enum, required, additionalProperties=False).
    """
    # 延迟导入（避免循环依赖）
    from ..ai_cmd import _mcp_debug_enter, _mcp_debug_exit
    _mcp_debug_enter("build_native_tools")

    native = [
        # ═══════════════════════════════════════════
        # ReadOnly — 安全只读，自动放行
        # ═══════════════════════════════════════════

        _make_tool(
            "get_file_info",
            "获取文件基本信息：大小、修改时间、行数、类型。修改文件前先调用此工具了解概况。",
            {"path": {"type": "string", "description": "文件路径"}},
            ["path"],
            PERM_READONLY,
        ),
        _make_tool(
            "read_file",
            "读取文件内容。支持行号范围 range、head、tail。超过 64 KiB 的大文件自动返回大纲模式（文件大小、前 80 行、符号大纲与钻取提示）。改文件前务必先读文件确认当前内容。",
            {
                "path": {"type": "string", "description": "文件路径"},
                "range": {"type": "string", "description": "可选行号范围，如 '10-30' 或 '42'（单行）"},
                "head": {"type": "integer", "description": "可选：只读前 N 行"},
                "tail": {"type": "integer", "description": "可选：只读末尾 N 行"},
            },
            ["path"],
            PERM_READONLY,
        ),
        _make_tool(
            "glob_search",
            "使用 glob 模式查找文件。如 'src/**/*.ts' 查找所有 TypeScript 文件。",
            {
                "pattern": {"type": "string", "description": "Glob 模式，如 'src/**/*.py'"},
                "path": {"type": "string", "description": "可选搜索根目录，默认当前工作目录"},
            },
            ["pattern"],
            PERM_READONLY,
        ),
        _make_tool(
            "grep_search",
            "用正则搜索文件内容，支持上下文行与大小写控制。",
            {
                "pattern": {"type": "string", "description": "搜索的正则表达式"},
                "path": {"type": "string", "description": "可选搜索根目录"},
                "glob": {"type": "string", "description": "可选文件过滤，如 '*.py'"},
                "context": {"type": "integer", "description": "可选上下各行数，默认 0"},
                "-i": {"type": "boolean", "description": "可选忽略大小写，默认 false"},
                "head_limit": {"type": "integer", "description": "可选结果数量上限"},
            },
            ["pattern"],
            PERM_READONLY,
        ),
        _make_tool(
            "search_file",
            "按文件名关键字在目录树中递归查找文件（自动跳过 node_modules/.git/__pycache__ 等依赖目录）。返回完整路径列表，不截断。",
            {
                "pattern": {"type": "string", "description": "文件名关键字（子串匹配，不区分大小写）或 glob 模式"},
                "path": {"type": "string", "description": "可选搜索根目录，默认当前工作目录"},
            },
            ["pattern"],
            PERM_READONLY,
        ),
        _make_tool(
            "ToolSearch",
            "搜索可用工具的名称或关键字。不知道用什么工具时调用此工具查找。",
            {"query": {"type": "string", "description": "搜索关键词，如 'file'、'search'、'web'"}},
            ["query"],
            PERM_READONLY,
        ),
        _make_tool(
            "Skill",
            "加载并执行一个技能剧本。技能是预定义的可复用操作流程。",
            {
                "skill": {"type": "string", "description": "技能名称"},
                "args": {"type": "string", "description": "可选参数"},
            },
            ["skill"],
            PERM_READONLY,
        ),
        _make_tool(
            "Sleep",
            "等待指定秒数。用于监控、等待异步操作等场景。",
            {"seconds": {"type": "integer", "minimum": 1, "description": "等待秒数"}},
            ["seconds"],
            PERM_READONLY,
        ),
        _make_tool(
            "StructuredOutput",
            "以请求的格式返回结构化数据。format='json'时返回 JSON 字符串。",
            {
                "format": {"type": "string", "enum": ["json"], "description": "输出格式"},
                "data": {"type": "string", "description": "要结构化的数据"},
            },
            ["format", "data"],
            PERM_READONLY,
        ),
        _make_tool(
            "TodoWrite",
            "更新会话任务列表，跟踪多步骤进度；status=completed 表示该步完成。",
            {
                "todos": {
                    "type": "array",
                    "description": "任务列表",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string", "description": "任务描述"},
                            "status": {"type": "string", "enum": ["pending", "in_progress", "completed"],
                                       "description": "任务状态"},
                            "activeForm": {"type": "string", "description": "进行中状态的动名词描述，如'正在分析架构'"},
                        },
                        "required": ["content", "status", "activeForm"],
                        "additionalProperties": False,
                    },
                }
            },
            ["todos"],
            PERM_WORKSPACE_WRITE,
        ),

        # ═══════════════════════════════════════════
        # WorkspaceWrite — 修改工作区，需轻确认
        # ═══════════════════════════════════════════

        _make_tool(
            "write_file",
            "创建新文件或全量覆盖（仅新建或 >70% 变动；局部修改用 edit_file）。>20KB 新文件先建骨架，再分多次 edit_file 填入。",
            {
                "path": {"type": "string", "description": "文件路径"},
                "content": {"type": "string", "description": "完整的文件内容"},
            },
            ["path", "content"],
            PERM_WORKSPACE_WRITE,
        ),
        _make_tool(
            "edit_file",
            "SEARCH/REPLACE 精确替换；old_string 须逐字节匹配且唯一；改前先 validate_edit 校验；保留缩进。写入大文件必须分块：骨架 + 多次 edit_file（每块 <200 行），禁止一次性全量 write_file。",
            {
                "path": {"type": "string", "description": "目标文件路径"},
                "old_string": {"type": "string", "description": "要替换的旧文本（逐字节精确匹配，必须唯一）"},
                "new_string": {"type": "string", "description": "替换后的新文本"},
                "replace_all": {"type": "boolean", "description": "可选：是否替换所有匹配项（默认只替换第一个）"},
            },
            ["path", "old_string", "new_string"],
            PERM_WORKSPACE_WRITE,
        ),
        _make_tool(
            "validate_edit",
            "校验 SEARCH 文本在目标文件中存在且唯一；每次 edit_file 前务必先调用。",
            {
                "file_path": {"type": "string", "description": "目标文件路径"},
                "search": {"type": "string", "description": "要搜索的旧文本（逐字节精确匹配）"},
                "replace": {"type": "string", "description": "替换后的新文本"},
            },
            ["file_path", "search", "replace"],
            PERM_READONLY,  # 校验是安全的
        ),
        _make_tool(
            "preview_edit",
            "预览 edit_file 的 unified diff，确认正确后再编辑。",
            {
                "file_path": {"type": "string", "description": "目标文件路径"},
                "search": {"type": "string", "description": "要搜索的旧文本"},
                "replace": {"type": "string", "description": "替换后的新文本"},
            },
            ["file_path", "search", "replace"],
            PERM_READONLY,  # 预览是安全的
        ),
        _make_tool(
            "remember",
            "标记 library 会话为重要（提升保留等级，不被压缩清理）。",
            {
                "session_id": {"type": "string", "description": "library 会话 UUID，如 abc123-def456"},
            },
            ["session_id"],
            PERM_READONLY,
        ),
        _make_tool(
            "forget",
            "归档 library 会话（移至 .archive/，可恢复）。",
            {
                "session_id": {"type": "string", "description": "library 会话 UUID"},
            },
            ["session_id"],
            PERM_READONLY,
        ),
        _make_tool(
            "memory",
            "操作 library 历史会话与时间线：search 按关键词搜索；list 列出活跃记忆，或传 day/month/year/start/end 查询时间线（当日任务/当月每日描述/当年每月描述/区间）；read 用 session_id 读完整记录。",
            {
                "operation": {"type": "string", "enum": ["search", "list", "read"], "description": "search/list/read"},
                "query": {"type": "string", "description": "搜索关键词（search 时必填）"},
                "session_id": {"type": "string", "description": "会话 UUID（read 时必填）"},
                "filter": {"type": "string", "description": "过滤 class 等级（list 时可选）"},
                "limit": {"type": "integer", "description": "返回结果数，默认 8，最大 20"},
                "day": {"type": "string", "description": "时间线：查询指定日 'YYYY-M-D'（如 2026-2-12）当日任务列表"},
                "month": {"type": "string", "description": "时间线：查询指定月 'YYYY-M'（如 2026-6）该月每日描述"},
                "year": {"type": "string", "description": "时间线：查询指定年 'YYYY'（如 2026）该年每月描述"},
                "start": {"type": "string", "description": "时间线：区间起始日 'YYYY-M-D'（配合 end 查询几日到几日的工作内容）"},
                "end": {"type": "string", "description": "时间线：区间结束日 'YYYY-M-D'"},
                "skill": {"type": "string", "description": "预留：按技能维度过滤时间线（当前版本仅透传）"},
            },
            ["operation"],
            PERM_READONLY,
        ),
        _make_tool(
            "compact_stats",
            "查看 library 压缩状态：活跃/归档数、估算 token、触发阈值。",
            {},
            [],
            PERM_READONLY,
        ),
        _make_tool(
            "choose_ask",
            '不确定用户意图时提供选项；用户可选「以上都不是」自由输入。',
            {
                "question": {"type": "string", "description": "向用户提出的问题"},
                "options": {
                    "type": "array",
                    "description": "选项列表（至少2个，最多6个）",
                    "items": {"type": "string"},
                    "minItems": 2,
                    "maxItems": 6,
                },
            },
            ["question", "options"],
            PERM_READONLY,
        ),
        _make_tool(
            "submit_plan",
            "提交计划给用户确认（系统门禁：大型写操作——单次 >4KB 或本轮累计 ≥8KB——与破坏性操作（删除/移动/复制/建目录）在确认前会被拦截；小型修改可直接执行）。plan 与 steps 二选一；确认后按步骤执行。",
            {
                "plan": {"type": "string", "description": "Markdown 格式的计划描述"},
                "steps": {
                    "type": "array",
                    "description": "结构化步骤列表",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "步骤 ID，如 step-1"},
                            "title": {"type": "string", "description": "简短标题"},
                            "action": {"type": "string", "description": "具体操作描述"},
                            "risk": {"type": "string", "enum": ["low", "med", "high"], "description": "风险等级"},
                        },
                        "required": ["id", "title"],
                        "additionalProperties": False,
                    },
                },
            },
            ["plan"],
            PERM_WORKSPACE_WRITE,
        ),
        _make_tool(
            "mark_step_complete",
            "标记一个步骤已完成。提交计划后每完成一步调用此工具更新进度。",
            {"step_id": {"type": "string", "description": "步骤 ID，如 step-1"}},
            ["step_id"],
            PERM_WORKSPACE_WRITE,
        ),
        _make_tool(
            "EnterPlanMode",
            "进入计划模式（禁止命令与文件修改，只能输出计划）；进入后应立即用 submit_plan 提交计划。",
            {},
            [],
            PERM_WORKSPACE_WRITE,
        ),
        _make_tool(
            "ExitPlanMode",
            "退出计划模式，恢复正常执行；计划确认后调用并开始执行。",
            {},
            [],
            PERM_WORKSPACE_WRITE,
        ),
        _make_tool(
            "Config",
            "获取或设置 Onyx 配置：get 返回当前配置，set 设置键值。"
            "注意：set 会写入 ~/.config/onyx/config.json（cwd 沙盒之外），需要用户显式批准。",
            {
                "action": {"type": "string", "enum": ["get", "set"], "description": "操作类型"},
                "key": {"type": "string", "description": "配置键名"},
                "value": {"type": "string", "description": "配置值（set 时需要）"},
            },
            ["action", "key"],
            PERM_DANGER_FULL,
        ),

        # ═══════════════════════════════════════════
        # ═══════════════════════════════════════════
        # DangerFullAccess — 危险操作，需显式批准
        # ═══════════════════════════════════════════

        _make_tool(
            "Agent",
            "启动子代理（隔离上下文，总结后喂回主 AI）。类型：explore=只读调查；plan=规划（只读+git）；lint=代码分析；test=测试；web_search_agent=联网调研（web_search 多重混合搜索+抓页）。所有类型均可经安全管线执行命令（危险命令与 Onyx 内置命令如 exit/clear/ai 不可用）。explore/plan 自动执行无需用户确认；lint/test/web_search_agent 需显式批准。适合大规模只读调查或可并行子任务——主上下文只接收总结，注意不要滥用。可指定 1~5 个任务并行（最多 5 个同时运行）。mode=sync 阻塞等待总结；mode=async 立即返回，完成后结果自动注入会话。**并行调查多个主题时，请用 `tasks` 数组在一次调用中派发，不要多次调用本工具。**",
            {
                "description": {"type": "string", "description": "子代理任务描述"},
                "prompt": {"type": "string", "description": "子代理的完整指令；多任务时可用 '1. ...\\n2. ...' 编号或 --- 分隔，配合 count 并行"},
                "name": {"type": "string", "description": "可选子代理名称"},
                "type": {"type": "string", "enum": ["explore", "plan", "lint", "test", "web_search_agent"], "description": "子代理类型（默认 explore）"},
                "mode": {"type": "string", "enum": ["sync", "async"], "description": "sync=等待完成并返回总结；async=后台运行，完成自动注入（默认 sync）"},
                "model": {"type": "string", "description": "可选模型名覆盖；plan 类型未指定时默认自动升档到同系列更强模型（如 flash→pro）"},
                "count": {"type": "integer", "description": "并行子代理数量 1~5（默认 1；tasks 存在时按 tasks 长度）"},
                "tasks": {
                    "type": "array",
                    "items": {
                        "oneOf": [
                            {"type": "string", "description": "任务指令（使用调用级 type/model）"},
                            {"type": "object", "properties": {
                                "prompt": {"type": "string", "description": "该子代理的任务指令（必填）"},
                                "type": {"type": "string", "enum": ["explore", "plan", "lint", "test", "web_search_agent"], "description": "可选：该子代理角色（决定系统提示词与工具集），默认与调用级 type 一致"},
                                "model": {"type": "string", "description": "可选：该子代理使用的模型"},
                                "name": {"type": "string", "description": "可选：该子代理名称（显示用）"},
                            }, "required": ["prompt"]},
                        ]
                    },
                    "description": "可选：1~5 个子任务；每个元素可为字符串（指令）或对象（prompt + 可选 type/model/name）——每个子代理独立提示词/角色/模型，完全独立工作",
                },
            },
            ["description", "prompt"],
            PERM_DANGER_FULL,
        ),
        _make_tool(
            "web_search",
            "网络调研全能工具（唯一 web 工具，旧 WebSearch/WebFetch 已合并）。三模式：search=仅多引擎搜索；fetch=抓取指定 urls 的页面正文；mixed=搜索+自动抓页（默认）。用法：先 search 看 snippet 摘要判断相关性，需要正文细节再 fetch_pages/mixed；queries 建议 ≤3 个；权威站点用 allowed_domains 限定。支持多查询 × 多引擎、域名双向过滤、语言/地区/时效、安全搜索、正文长度控制、text/json 双输出。引擎可用性自动降级、结果带短时缓存，无需额外处理。可选 ai_assist=长文弱 AI 摘要开关（缺省跟随全局 web_ai_assist）。查资料、查文档、对比信息首选。自动执行。",
            {
                "action": {"type": "string", "enum": ["search", "fetch", "mixed"], "description": "操作模式：search=仅搜索；fetch=仅抓取 urls 指定页面；mixed=搜索+抓取（默认）"},
                "ai_assist": {"type": "boolean", "description": "长文弱 AI 摘要：true=长文完整交给辅助 AI 总结后返回摘要；false=关键行压缩；缺省=跟随全局开关 web_ai_assist（Config 工具设置）"},
                "query": {"type": "string", "description": "主搜索查询（action=search/mixed 必填；fetch 模式可省略）"},
                "queries": {"type": "array", "items": {"type": "string"}, "description": "附加查询列表（混合查资料：一次覆盖多个角度，最多 10 个）"},
                "topics": {
                    "type": "array",
                    "maxItems": 5,
                    "items": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "该主题的搜索查询（必填）"},
                            "engines": {"type": "array", "items": {"type": "string", "enum": ["duckduckgo", "bing"]}, "description": "该主题搜索引擎（默认继承顶层）"},
                            "max_results": {"type": "integer", "minimum": 1, "maximum": 15, "description": "该主题条数上限（默认继承顶层）"},
                            "fetch_pages": {"type": "boolean", "description": "该主题是否自动抓页（默认继承顶层）"},
                            "fetch_limit": {"type": "integer", "minimum": 1, "maximum": 5, "description": "该主题抓页上限（默认继承顶层）"},
                            "max_chars_per_page": {"type": "integer", "minimum": 500, "maximum": 8000, "description": "该主题单页字符上限（默认继承顶层）"},
                            "ai_assist": {"type": "boolean", "description": "该主题长文摘要开关（默认继承顶层）"},
                            "allowed_domains": {"type": "array", "items": {"type": "string"}, "description": "该主题域名白名单（默认继承顶层）"},
                            "exclude_domains": {"type": "array", "items": {"type": "string"}, "description": "该主题域名黑名单（默认继承顶层）"},
                        },
                        "required": ["query"],
                    },
                    "description": "批量独立主题（最多 5 个）：一次并行查询多个互不相关的主题，每主题独立搜索+抓页+分栏输出；与 queries 不同——queries 是同一主题的多角度，topics 是多个独立主题",
                },
                "urls": {"type": "array", "items": {"type": "string"}, "description": "指定 URL 列表直接抓取正文（action=fetch 必填；mixed 时追加抓取；同样过域名过滤与 SSRF 防护）"},
                "engines": {"type": "array", "items": {"type": "string", "enum": ["duckduckgo", "bing"]}, "description": "搜索引擎列表（默认两者都用）"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 15, "description": "每个查询每个引擎返回条数上限（默认 8）"},
                "allowed_domains": {"type": "array", "items": {"type": "string"}, "description": "仅保留这些域名下的结果（如 github.com）"},
                "exclude_domains": {"type": "array", "items": {"type": "string"}, "description": "排除这些域名下的结果"},
                "language": {"type": "string", "description": "语言偏好（如 zh/en，best-effort）"},
                "region": {"type": "string", "description": "地区偏好（如 cn-zh/us-en，best-effort）"},
                "time_range": {"type": "string", "enum": ["day", "week", "month", "year"], "description": "时效过滤（best-effort，仅支持的引擎生效）"},
                "safe_search": {"type": "boolean", "description": "安全搜索：严格模式过滤成人内容（默认 false）"},
                "fetch_pages": {"type": "boolean", "description": "搜索后自动抓取排名靠前结果的页面正文（默认 false）"},
                "fetch_limit": {"type": "integer", "minimum": 1, "maximum": 5, "description": "自动抓取页数上限 1~5（默认 3）"},
                "max_chars_per_page": {"type": "integer", "minimum": 500, "maximum": 8000, "description": "单页正文最大字符数（默认 3000）"},
                "output_format": {"type": "string", "enum": ["text", "json"], "description": "输出格式：text=易读文本；json=结构化数据（默认 text）"},
                "timeout": {"type": "integer", "minimum": 5, "maximum": 60, "description": "单请求超时秒数（默认 15）"},
            },
            [],
            PERM_READONLY,
        ),
    ]

    # ═══════════════════════════════════════════
    # Task System — 任务管理（TaskPacket + 6态状态机 + 团队 + Cron）
    # ═══════════════════════════════════════════
    for _task_tool_def in [
        ("TaskCreate",
         "创建结构化任务：传 prompt 建简单任务，或传 TaskPacket 字段（objective/scope/acceptance_criteria 等）建完整任务包。返回任务 ID。",
         {
             "prompt": {"type": "string", "description": "任务描述（简单模式），或 TaskPacket.objective"},
             "description": {"type": "string", "description": "可选任务说明"},
             "scope": {"type": "string", "enum": ["workspace", "module", "single_file", "custom"],
                       "description": "任务作用域（默认 workspace）"},
             "scope_path": {"type": "string", "description": "作用域路径（module/single_file/custom 时需要）"},
             "acceptance_criteria": {"type": "array", "items": {"type": "string"},
                                      "description": "验收标准列表"},
             "acceptance_tests": {"type": "array", "items": {"type": "string"},
                                   "description": "验收测试命令列表"},
             "verification_plan": {"type": "array", "items": {"type": "string"},
                                    "description": "验证步骤"},
             "resources": {"type": "array", "items": {"type": "object",
                           "properties": {"kind": {"type": "string"}, "value": {"type": "string"}},
                           "additionalProperties": False},
                           "description": "允许访问的资源列表"},
             "model": {"type": "string", "description": "指定模型"},
             "provider": {"type": "string", "description": "模型提供商"},
             "commit_policy": {"type": "string", "description": "提交策略"},
             "branch_policy": {"type": "string", "description": "分支策略"},
             "reporting_contract": {"type": "string", "description": "报告合同"},
             "escalation_policy": {"type": "string", "description": "升级策略"},
             "recovery_policy": {"type": "string", "description": "恢复策略"},
         },
         ["prompt"], PERM_WORKSPACE_WRITE),

        ("TaskList",
         "列出任务列表，可选按状态过滤。状态值：created, running, blocked, completed, failed, stopped。",
         {"status_filter": {"type": "string", "description": "可选状态过滤"}},
         [], PERM_WORKSPACE_WRITE),

        ("TaskGet",
         "查看单个任务的详细信息，包括消息记录和输出。",
         {"task_id": {"type": "string", "description": "任务 ID"}},
         ["task_id"], PERM_WORKSPACE_WRITE),

        ("TaskUpdate",
         "更新任务状态或追加消息。status 可选值：created, running, blocked, completed, failed, stopped。",
         {"task_id": {"type": "string", "description": "任务 ID"},
          "status": {"type": "string", "description": "新状态"},
          "message": {"type": "string", "description": "可选追加的消息内容"}},
         ["task_id"], PERM_WORKSPACE_WRITE),

        ("TaskStop",
         "终止一个任务。只能终止非终态（completed/failed/stopped）的任务。",
         {"task_id": {"type": "string", "description": "任务 ID"}},
         ["task_id"], PERM_WORKSPACE_WRITE),

        ("TaskBoard",
         "查看看板视图 — 按 active（created/running）/ blocked / finished 三栏展示所有任务及其心跳状态。",
         {},
         [], PERM_READONLY),

        ("TaskRemove",
         "从注册表中删除一个任务。不可恢复。",
         {"task_id": {"type": "string", "description": "任务 ID"}},
         ["task_id"], PERM_WORKSPACE_WRITE),

        ("TeamCreate",
         "创建一个团队，可选择关联的任务 ID 列表。",
         {"name": {"type": "string", "description": "团队名称"},
          "task_ids": {"type": "array", "items": {"type": "string"},
                        "description": "可选关联任务 ID 列表"}},
         ["name"], PERM_WORKSPACE_WRITE),

        ("TeamList",
         "列出所有团队。",
         {}, [], PERM_READONLY),

        ("TeamDelete",
         "删除一个团队（软删除）。",
         {"team_id": {"type": "string", "description": "团队 ID"}},
         ["team_id"], PERM_WORKSPACE_WRITE),

        ("CronCreate",
         "创建一个定时任务条目。schedule 为 cron 表达式，如 '0 * * * *'（每小时）。"
         "注意：定时任务到点会以 shell 形式执行 prompt，创建需要用户显式批准。",
         {"schedule": {"type": "string", "description": "cron 表达式"},
          "prompt": {"type": "string", "description": "定时执行的任务描述"},
          "description": {"type": "string", "description": "可选说明"}},
         ["schedule", "prompt"], PERM_DANGER_FULL),

        ("CronList",
         "列出所有定时任务，可选仅显示启用的。",
         {"enabled_only": {"type": "boolean", "description": "是否只显示启用的条目（默认 false）"}},
         [], PERM_READONLY),

        ("CronDisable",
         "禁用一个定时任务，停止其调度执行。",
         {"cron_id": {"type": "string", "description": "定时任务 ID"}},
         ["cron_id"], PERM_WORKSPACE_WRITE),

        ("CronDelete",
         "删除一个定时任务。",
         {"cron_id": {"type": "string", "description": "定时任务 ID"}},
         ["cron_id"], PERM_WORKSPACE_WRITE),
    ]:
        native.append(_make_tool(*_task_tool_def))

    # ═══════════════════════════════════════════
    # ═══════════════════════════════════════════
    # 代码分析工具 — 定义位于 bin/ai_lib/tools/code_analysis.py
    # ═══════════════════════════════════════════

    native.extend(code_analysis.get_native_tools(_make_tool))

    # ═══════════════════════════════════════════
    # Memory — 记忆查询工具（支持 range + context）
    # ═══════════════════════════════════════════

    native.append(_make_tool(
        "MemoryRead",
        "读取记忆文件，支持行号范围。路径示例：chat/first、library/<uuid>、onyx_ai。结果自动缓存。",
        {
            "path": {"type": "string", "description": "记忆路径（如 chat/first, library/<uuid>, onyx_ai）"},
            "range": {"type": "string", "description": "可选行号范围，如 '1-30' 或 '50'（单行）"},
        },
        ["path"], PERM_READONLY,
    ))
    native.append(_make_tool(
        "MemorySearch",
        "在记忆文件中搜关键字，默认显示匹配行上下各 3 行（含行号）；uuid 指定单个会话或 all 全范围。结果自动缓存。",
        {
            "pattern": {"type": "string", "description": "搜索关键字或正则"},
            "uuid": {"type": "string", "description": "目标记忆 UUID，或 'all' 表示全范围查找（默认 all）"},
            "context": {"type": "integer", "description": "可选上下文行数，默认 3"},
            "-i": {"type": "boolean", "description": "可选忽略大小写，默认 true"},
        },
        ["pattern"], PERM_READONLY,
    ))

    native.append(_make_tool(
        "UndoLastEdit",
        "撤销上一次文件编辑或写入操作。将文件恢复为修改前的内容。只能在有可撤销记录时使用。",
        {},
        [], PERM_WORKSPACE_WRITE,
    ))

    # ── Include non-filesystem MCP tools (puppeteer/github/postgres etc.) ──
    # 延迟导入（避免循环依赖）
    from ..ai_cmd import get_mcp_tools
    mcp_tools = get_mcp_tools(user_home_dir=user_home_dir)
    if mcp_tools:
        seen_names = {t["function"]["name"] for t in native if "function" in t}
        for mt in mcp_tools:
            name = mt.get("name", "")
            if not name:
                continue
            # ── MCP 工具名归一化：mcp_registry 返回 "mcp__<server>__<tool>"，
            #    而 MCP_TOOLS_CACHE 回退路径返回 "<tool>"。两条路径若产出不同
            #    名称，tools 数组会随注册表填充状态变化（59↔68 个、单/双前缀），
            #    直接打断跨会话前缀缓存。统一取最后一段，保证工具表在任何
            #    状态下字节级一致。──
            if name.startswith("mcp__"):
                name = name.rsplit("__", 1)[-1]
            if not name or name in seen_names:
                continue
            if name in ("read_file", "write_file", "edit_file",
                         "create_directory", "list_directory",
                         "directory_tree", "move_file", "copy_file",
                         "delete_file", "delete_directory",
                         "get_file_info", "search_files", "search_content",
                         "glob", "find_on_path", "get_workspace_folders"):
                continue
            mcp_prefixed = f"mcp_{name}"
            native.append({
                "type": "function",
                "function": {
                    "name": mcp_prefixed,
                    "description": mt.get("description", ""),
                    "parameters": mt.get("inputSchema", {}),
                },
                "x_permission": PERM_READONLY,  # 2026-09 用户拍板：MCP 工具一律免手动确认（ReadOnly 全模式自动放行）
            })
            seen_names.add(mcp_prefixed)

    # ── 目录浏览工具 ──
    native.append(_make_tool(
        "ListDirectory",
        "List files and directories in a path. Returns one entry per line, directories marked with /.",
        {"path": {"type": "string", "description": "Directory path to list, defaults to current directory"}},
        [],
        PERM_READONLY,
    ))
    native.append(_make_tool(
        "DirectoryTree",
        "Recursively show directory tree structure. Dirs marked with /, max depth 2 by default.",
        {
            "path": {"type": "string", "description": "Root directory, defaults to current directory"},
            "maxDepth": {"type": "integer", "description": "Max recursion depth, default 2, max 5"},
        },
        [],
        PERM_READONLY,
    ))

    # ── Git 工具 ──
    native.append(_make_tool(
        "GitStatus",
        "显示 Git 工作区状态（相当于 git status --short）。返回已修改/新增/删除的文件列表。",
        {"path": {"type": "string", "description": "Git 仓库路径，默认当前目录"}},
        [],
        PERM_READONLY,
    ))
    native.append(_make_tool(
        "GitDiff",
        "显示 Git 未暂存的变更（相当于 git diff）。返回文件级别的 diff 内容。",
        {
            "path": {"type": "string", "description": "Git 仓库路径，默认当前目录"},
            "staged": {"type": "boolean", "description": "是否显示已暂存变更（git diff --staged），默认 false"},
        },
        [],
        PERM_READONLY,
    ))
    native.append(_make_tool(
        "GitLog",
        "查看 Git 提交历史（相当于 git log --oneline）。返回最近的提交记录。",
        {
            "path": {"type": "string", "description": "Git 仓库路径，默认当前目录"},
            "count": {"type": "integer", "description": "显示条数，默认 10"},
        },
        [],
        PERM_READONLY,
    ))
    native.append(_make_tool(
        "GitBranch",
        "查看 Git 分支信息（相当于 git branch -a）。返回所有本地和远程分支。",
        {"path": {"type": "string", "description": "Git 仓库路径，默认当前目录"}},
        [],
        PERM_READONLY,
    ))

    # ── Shell 命令执行（function calling）──
    # 命令经 Onyx 安全管线执行：危险命令弹用户确认、输出捕获后以 tool 结果
    # 回传。ReadOnly 权限仅用于跳过工具门控——真正的安全确认在 handler 内部
    # （is_dangerous_command → confirm_dangerous_command）。
    native.append(_make_tool(
        "EnvProbe",
        "只读环境探测（秒回）。type 按任务类型动态调整探测范围：deploy=部署/批量、network=网络、python=Python 环境、build=编译链、database=数据库客户端、web=Web/Node、permission=权限专项，缺省 general=全量报告。which 查询指定命令的路径与版本（空格/逗号分隔多个；仅传 which 时输出轻量摘要）。仅用于相对郑重的任务（部署、批量操作、跨平台命令、权限敏感操作）或对环境不确定时——在规划获批后、执行命令前探测，可避免平台差异、权限限制、工具缺失导致的失败；简单命令无需探测。",
        {
            "type": {"type": "string", "description": "任务类型（可选，默认 general 全量），支持逗号组合多个：general=全量 / deploy=部署批量 / network=网络渗透（扫描/爆破/嗅探/无线）/ python=Python 环境 / build=编译构建 / database=数据库客户端 / web=Web 渗透（目录/漏洞/指纹）与前端 / permission=权限专项。示例：'web,network' 同时探测两者"},
            "which": {"type": "string", "description": "可选：要查询的命令名，空格或逗号分隔多个，返回路径与版本；仅传此参数时输出轻量查询结果"},
        },
        [], PERM_READONLY,
    ))
    native.append(_make_tool(
        "RunCommand",
        "Execute a shell command through Onyx's security pipeline. Output (stdout+stderr) and exit code are captured and returned; dangerous commands require user confirmation. Command construction rules: (1) NEVER assume tools exist — run EnvProbe first, respect platform differences (Android/Termux lacks ip/ss → use ifconfig/netstat; Windows lacks grep/uname); (2) probe before relying: `which X` or `X --version` when unsure; (3) keep output bounded — append `2>&1 | tail -50` for long-output commands; (4) one logical operation per call; chain freely with &&/||/;. Non-root: nmap -O/-sU quit entirely — avoid them.",
        {"command": {"type": "string", "description": "Shell command to execute (single line)"}},
        ["command"], PERM_READONLY,
    ))

    # ── AI 插件工具（~/.ai_s/plugin_tool/index.json 注册的 C 插件）──
    # 通过各插件的 plugin_tool_schema 接口（已缓存 schema）注入 AI 工具列表，
    # 使 AI 能感知并调用注册的 C 插件工具（如 mem_proc_monitor）。
    try:
        from bin.plugin_loader import plugin_tools_schemas, _index_entry
    except ImportError:
        try:
            from ..plugin_loader import plugin_tools_schemas, _index_entry
        except Exception:
            plugin_tools_schemas = None
            _index_entry = None
    if plugin_tools_schemas is not None:
        try:
            _plugin_schemas = plugin_tools_schemas()
            for _pname in sorted(_plugin_schemas):
                _sch = _plugin_schemas[_pname]
                _pentry = _index_entry(_pname) or {}
                _perm = _pentry.get("permission", PERM_DANGER_FULL)
                native.append(_make_tool(
                    _pname,
                    _sch.get("description") or f"AI 插件工具 {_pname}",
                    _sch.get("properties") or {},
                    _sch.get("required") or [],
                    _perm,
                ))
        except Exception:
            pass

    native.sort(key=lambda t: t.get("function", {}).get("name", ""))
    _mcp_debug_exit("build_native_tools", ok=len(native) > 0,
                    detail=f"{len(native)} native tools")
    return native


# ──────────────────── 工具表冻结缓存（前缀缓存稳定性）────────────────────

# native_tools 若在每个 handle_ai 调用时重建，MCP registry 的异步填充会让
# 工具数组在 REPL 跨轮之间变化（59↔68 个）；tools 位于请求最前端（model
# 之后），一变即整轮前缀分叉 → 缓存 0% 命中。
# 模块级缓存：同一 (user_home_dir, mcp_enabled) 下首次构建后冻结，
# 只有 MCP 连接成功写入 registry 或 mcp 开关切换（key 变化）时才重建。
_NATIVE_TOOLS_CACHE: Dict[str, Any] = {"key": None, "tools": None, "prompt": None}

# 工具名 → x_permission 惰性映射（从冻结工具表构建一次；随 invalidate 失效）。
# execute_mcp_tool 的权限门禁查它——避免每次工具调用都全量重建 build_native_tools()
# （重建会重新遍历全部工具描述，且可能触发 MCP 发现/连接，是慢路径）。
_TOOL_PERMISSION_LOOKUP: Dict[str, str] = {}


def _get_tool_permission(tool_name: str) -> str:
    """从缓存工具表查 x_permission，缺失时回退 ReadOnly（安全默认）。"""
    if not _TOOL_PERMISSION_LOOKUP:
        try:
            _tools, _ = get_native_tools_cached(USER_HOME_DIR, True)
            for _t in _tools:
                _n = _t.get("function", {}).get("name", "")
                if _n:
                    _TOOL_PERMISSION_LOOKUP[_n] = _t.get("x_permission", PERM_READONLY)
        except Exception:
            pass
    return _TOOL_PERMISSION_LOOKUP.get(tool_name, PERM_READONLY)


def invalidate_native_tools_cache() -> None:
    """MCP 工具表变化（新连接/Registry 更新）后调用，强制下次重建。"""
    _NATIVE_TOOLS_CACHE["key"] = None
    _NATIVE_TOOLS_CACHE["tools"] = None
    _NATIVE_TOOLS_CACHE["prompt"] = None
    _TOOL_PERMISSION_LOOKUP.clear()


def get_native_tools_cached(user_home_dir: str, mcp_enabled: bool) -> tuple:
    """返回 (tools, tools_prompt)，跨 handle_ai 冻结，保证 tools 数组字节稳定。"""
    # 延迟导入（避免循环依赖）
    from ..ai_cmd import _mcp_debug
    key = (user_home_dir, bool(mcp_enabled))
    if _NATIVE_TOOLS_CACHE["key"] == key and _NATIVE_TOOLS_CACHE["tools"] is not None:
        return _NATIVE_TOOLS_CACHE["tools"], _NATIVE_TOOLS_CACHE["prompt"]
    tools = build_native_tools(user_home_dir)
    prompt = build_native_tools_prompt()
    _NATIVE_TOOLS_CACHE["key"] = key
    _NATIVE_TOOLS_CACHE["tools"] = tools
    _NATIVE_TOOLS_CACHE["prompt"] = prompt
    _mcp_debug(f"native_tools 冻结缓存: {len(tools)} 个工具 (mcp_enabled={mcp_enabled})")
    return tools, prompt
