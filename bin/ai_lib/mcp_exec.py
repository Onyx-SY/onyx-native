# -*- coding: utf-8 -*-
"""
mcp_exec.py — 工具执行分发器（execute_mcp_tool）+ MCP 服务器管理命令

从 bin/ai_cmd.py 拆分（模块化架构重构）：
- 内置 handler 表优先 → MCP 协议（外部 server）兜底；
- 已拆执行器（tool_executors/memory_tools/web_search/env_probe）模块级导入；
- 仍由 ai_cmd 持有的执行器（_exec_run_command/_exec_agent/_exec_config 等）与
  审批账本（_APPROVAL_LEDGER）在函数体内延迟导入，避免循环依赖。
"""

import json
import os
import re
import shutil
import subprocess
import time
from typing import Callable, Dict, List, Optional, Tuple

from lib.approval_tokens import ApprovalScope
from rich.console import Console

from . import sandbox
from .config import get_current_lang
from .env_probe import _exec_env_probe
from .mcp_client_core import (
    MCP_SERVER_PROCESSES, MCP_TOOLS_CACHE,
    _cleanup_mcp_stderr_buffers, _ensure_dir, _load_mcp_config,
    _mcp_debug, _mcp_request, _mcp_t, _save_mcp_config,
    connect_mcp_server, is_mcp_server_running,
)
from .mcp_registry import get_registry
from .memory_tools import (
    _exec_compact_stats, _exec_forget_session, _exec_list_hippocampus,
    _exec_list_timeline, _exec_memory_read, _exec_memory_search,
    _exec_read_memory, _exec_remember_session, _exec_search_library,
)
from .native_tools import PERM_DANGER_FULL, PERM_READONLY, _get_tool_permission
from .sandbox import SandboxBlockError as _SandboxBlockError
from .tool_executors import (
    _exec_cron_create, _exec_cron_delete, _exec_cron_disable, _exec_cron_list,
    _exec_directory_tree, _exec_edit_file, _exec_get_file_info, _exec_git_branch,
    _exec_git_diff, _exec_git_log, _exec_git_status, _exec_glob_search,
    _exec_grep_search, _exec_list_directory, _exec_preview_edit, _exec_read_file,
    _exec_search_file, _exec_skill, _exec_sleep, _exec_structured_output,
    _exec_task_board, _exec_task_create, _exec_task_get, _exec_task_list,
    _exec_task_remove, _exec_task_stop, _exec_task_update, _exec_team_create,
    _exec_team_delete, _exec_team_list, _exec_todo_write, _exec_tool_search,
    _exec_undo_last_edit, _exec_validate_edit, _exec_write_file,
)
from .tools import code_analysis
from .web_search import _exec_web_search_multi

console = Console()


def _unescape_json_fragment(s: str) -> str:
    """单遍左到右反转义 JSON 字符串片段（破损 JSON 容错路径用）。

    正确处理字面量 \\n（JSON 中编码为 \\\\n）与真实换行 \\n 的区别；
    未知转义保留原样，\\uXXXX 按码点解码。
    """
    out = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n:
            nxt = s[i + 1]
            if nxt == "n":
                out.append("\n"); i += 2; continue
            if nxt == "t":
                out.append("\t"); i += 2; continue
            if nxt == "r":
                out.append("\r"); i += 2; continue
            if nxt == '"':
                out.append('"'); i += 2; continue
            if nxt == "\\":
                out.append("\\"); i += 2; continue
            if nxt == "/":
                out.append("/"); i += 2; continue
            if nxt == "u" and i + 5 < n:
                try:
                    out.append(chr(int(s[i + 2:i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    pass
            out.append(nxt)
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def execute_mcp_tool(tool_name: str, params: Dict, name: str = "filesystem",
                     user_mode: str = "low", user_home_dir: str = None,
                     path_validator: Callable = None) -> Tuple[bool, str]:
    """
    执行工具调用。
    优先级：内置 handler → MCP 协议（外部 server）

    工具名规则：
      - 裸名（read_file）→ 先查内置 handler，再查 MCP server
      - mcp_xxx（mcp_puppeteer_navigate）→ 路由到 MCP server，调用 xxx
      - mcp__server__xxx（旧格式）→ 兼容旧版 MCP 调用
    """
    # ── 工具名解析 ──
    # mcp_xxx → MCP 工具，去掉 mcp_ 后路由到 server
    # 裸名 → 先查内置 handler，再查 MCP server（默认走 filesystem）
    from ..ai_cmd import (_exec_run_command, _exec_agent, _exec_config, _exec_choose_ask, _exec_enter_plan_mode, _exec_exit_plan_mode, _APPROVAL_LEDGER)
    raw_tool = tool_name
    mcp_server = name  # 保留调用者指定的 server 名
    # mcp_xxx（单下划线）→ 新版 MCP 前缀，去掉 mcp_ 后路由到 server
    if raw_tool.startswith("mcp_") and not raw_tool.startswith("mcp__"):
        raw_tool = raw_tool[4:]
        mcp_server = None
        try:
            _registry = get_registry()
            for _srv in _registry.server_names():
                if _registry.get(f"mcp__{_srv}__{raw_tool}"):
                    mcp_server = _srv
                    break
        except Exception:
            pass
    # mcp__server__xxx（旧格式）→ 取最后一段工具名
    if raw_tool.startswith("mcp__"):
        raw_tool = raw_tool.rsplit("__", 1)[-1]

    # ── AI 虚拟沙盒：文件类工具路径参数（虚拟 → 物理，越界拦截）──
    # 将 AI 视角的虚拟根 / 映射为用户 cwd；../ 逃逸等越界路径直接拒绝。
    if raw_tool in AI_FILE_TOOLS and sandbox.is_active():
        try:
            sandbox.resolve_many(params or {})
        except _SandboxBlockError as _sbe:
            return False, _sbe.message

    # ── 内置分析工具（不经过 MCP，直接 Python 执行）──
    # 用剥离后的 raw_tool 匹配
    _BUILTIN_HANDLERS = {
        # ── 文件操作 ──
        "validate_edit": lambda p: _exec_validate_edit(p.get("file_path", ""), p.get("search", ""), p.get("replace", "")),
        "preview_edit": lambda p: _exec_preview_edit(p.get("file_path", ""), p.get("search", ""), p.get("replace", "")),
        "get_file_info": lambda p: _exec_get_file_info(p.get("path", "")),
        "read_file":    lambda p: _exec_read_file(p.get("path", ""), p.get("range", None), p.get("head", None), p.get("tail", None)),
        "write_file":   lambda p: _exec_write_file(p.get("path", ""), p.get("content", "")),
        "edit_file":    lambda p: _exec_edit_file(p.get("path", ""), p.get("old_string", ""), p.get("new_string", "")),
        "glob_search":  lambda p: _exec_glob_search(p.get("pattern", ""), p.get("path", None)),
        "grep_search":  lambda p: _exec_grep_search(p.get("pattern", ""), p.get("path", None), p.get("glob", None),
                                                     p.get("context", 0), p.get("-i", False), p.get("head_limit", None)),
        "search_file":  lambda p: _exec_search_file(p.get("pattern", ""), p.get("path", None)),
        # ── 搜索与发现 ──
        "ToolSearch":   lambda p: _exec_tool_search(p.get("query", "")),
        "EnvProbe":     lambda p: _exec_env_probe(p.get("type", ""), p.get("which", "")),
        "Skill":        lambda p: _exec_skill(p.get("skill", ""), p.get("args", "")),
        # ── 计划与任务 ──
        "submit_plan":   lambda p: json.dumps({"plan": p.get("plan", ""), "steps": p.get("steps", [])}, ensure_ascii=False),
        "mark_step_complete": lambda p: p.get("step_id", ""),
        "TodoWrite":    lambda p: _exec_todo_write(p.get("todos", [])),
        "EnterPlanMode": lambda p: _exec_enter_plan_mode(),
        "ExitPlanMode":  lambda p: _exec_exit_plan_mode(),
        # ── 用户选择提问 ──
        "choose_ask":    lambda p: _exec_choose_ask(p.get("question", ""), p.get("options", [])),
        # ── Library 记忆管理 ──
        "remember":     lambda p: _exec_remember_session(p.get("session_id", "")),
        "forget":       lambda p: _exec_forget_session(p.get("session_id", "")),
        "memory":       lambda p: (
            _exec_search_library(p.get("query", ""), p.get("limit", 8))
            if p.get("operation", "search") == "search"
            else _exec_read_memory(p.get("session_id", ""))
            if p.get("operation") == "read"
            else _exec_list_timeline(
                p.get("day", ""), p.get("month", ""), p.get("year", ""),
                p.get("start", ""), p.get("end", ""), p.get("skill", ""))
            if p.get("operation") == "list" and (
                p.get("day") or p.get("month") or p.get("year")
                or p.get("start") or p.get("end"))
            else _exec_list_hippocampus(p.get("filter"), p.get("limit", 30))
        ),
        "compact_stats": lambda p: _exec_compact_stats(),
        # ── 配置 ──
        "Config":       lambda p: _exec_config(p.get("action", "get"), p.get("key", ""), p.get("value", None)),
        # ── 子代理与输出 ──
        "Agent":        lambda p: _exec_agent(p.get("description", ""), p.get("prompt", ""), p.get("name", ""),
                                              p.get("mode", "sync"), p.get("model", ""),
                                              p.get("count", 1), p.get("tasks", None),
                                              p.get("type", "explore")),
        "StructuredOutput": lambda p: _exec_structured_output(p.get("format", "json"), p.get("data", "")),
        "Sleep":        lambda p: _exec_sleep(int(p.get("seconds", 1))),
        # ── Web ──
        "web_search":   lambda p: _exec_web_search_multi(p),
        # ── 任务系统 ──
        "TaskCreate":   lambda p: _exec_task_create(
            p.get("prompt", ""), p.get("description"),
            p.get("scope"), p.get("scope_path"),
            p.get("acceptance_criteria"), p.get("acceptance_tests"),
            p.get("verification_plan"), p.get("resources"),
            p.get("model"), p.get("provider"),
            p.get("commit_policy"), p.get("branch_policy"),
            p.get("reporting_contract"), p.get("escalation_policy"),
            p.get("recovery_policy")),
        "TaskList":     lambda p: _exec_task_list(p.get("status_filter")),
        "TaskGet":      lambda p: _exec_task_get(p.get("task_id", "")),
        "TaskUpdate":   lambda p: _exec_task_update(
            p.get("task_id", ""), p.get("status"), p.get("message")),
        "TaskStop":     lambda p: _exec_task_stop(p.get("task_id", "")),
        "TaskBoard":    lambda p: _exec_task_board(),
        "TaskRemove":   lambda p: _exec_task_remove(p.get("task_id", "")),
        "TeamCreate":   lambda p: _exec_team_create(p.get("name", ""), p.get("task_ids")),
        "TeamList":     lambda p: _exec_team_list(),
        "TeamDelete":   lambda p: _exec_team_delete(p.get("team_id", "")),
        "CronCreate":   lambda p: _exec_cron_create(
            p.get("schedule", ""), p.get("prompt", ""), p.get("description")),
        "CronList":     lambda p: _exec_cron_list(p.get("enabled_only", False)),
        "CronDisable":  lambda p: _exec_cron_disable(p.get("cron_id", "")),
        "CronDelete":   lambda p: _exec_cron_delete(p.get("cron_id", "")),

        # ── 代码分析工具（bin/ai_lib/tools/code_analysis.py） ──
        "py_diagnostics": lambda p: code_analysis.exec_py_diagnostics(p.get("path", "")),
        "py_symbols":     lambda p: code_analysis.exec_py_symbols(p.get("path", "")),
        "LspDiagnostics": lambda p: code_analysis.exec_lsp_diagnostics(p.get("path", "")),
        "LspSymbols":     lambda p: code_analysis.exec_lsp_symbols(p.get("path", "")),
        "LspHover":       lambda p: code_analysis.exec_lsp_hover(
            p.get("path", ""), p.get("line", 0), p.get("character", 0)),
        "LspDefinition":  lambda p: code_analysis.exec_lsp_definition(
            p.get("path", ""), p.get("line", 0), p.get("character", 0)),
        "LspReferences":  lambda p: code_analysis.exec_lsp_references(
            p.get("path", ""), p.get("line", 0), p.get("character", 0)),
        "LspCompletion":  lambda p: code_analysis.exec_lsp_completion(
            p.get("path", ""), p.get("line", 0), p.get("character", 0)),
        "LspFormat":      lambda p: code_analysis.exec_lsp_format(p.get("path", "")),

        # ── 记忆查询 ──
        "MemoryRead":     lambda p: _exec_memory_read(p.get("path", ""), p.get("range")),
        "MemorySearch":   lambda p: _exec_memory_search(
            p.get("pattern", ""), p.get("uuid", "all"), int(p.get("context", 3)),
            p.get("-i", True)),
        "UndoLastEdit":   lambda p: _exec_undo_last_edit(),

        # ── 目录浏览工具 ──
        "ListDirectory": lambda p: _exec_list_directory(p.get("path", "")),
        "DirectoryTree":  lambda p: _exec_directory_tree(p.get("path", ""), int(p.get("maxDepth", 2))),

        # ── Git 工具 ──
        "GitStatus":  lambda p: _exec_git_status(p.get("path", "")),
        "GitDiff":    lambda p: _exec_git_diff(p.get("path", ""), p.get("staged", False)),
        "GitLog":     lambda p: _exec_git_log(p.get("path", ""), int(p.get("count", 10))),
        "GitBranch":  lambda p: _exec_git_branch(p.get("path", "")),
        # ── Shell 命令执行（function calling）──
        "RunCommand": lambda p: _exec_run_command(p.get("command", "")),
    }

    # ── AI 插件工具（~/.ai_s/plugin_tool/index.json 注册的 C 插件）──
    # 把注册插件名注入内置 handler 表，工具调用分发到 execute_plugin_tool。
    try:
        from ..plugin_loader import plugin_tools_schemas, execute_plugin_tool
    except ImportError:
        try:
            from bin.plugin_loader import plugin_tools_schemas, execute_plugin_tool
        except Exception:
            plugin_tools_schemas = None
            execute_plugin_tool = None
    if plugin_tools_schemas is not None:
        try:
            for _pname in plugin_tools_schemas():
                if _pname not in _BUILTIN_HANDLERS:
                    _BUILTIN_HANDLERS[_pname] = (lambda _n: (lambda p: execute_plugin_tool(_n, p)))(_pname)
        except Exception:
            pass

    # ── write_file 容错：如果参数被 _parse_tool_params 回退成 range_str，尝试从原始 JSON 中抠出 path 和 content ──
    # （位于统一门禁之前：write_file 是内置工具，容错必须在分发前修复参数）
    if raw_tool == "write_file" and "content" not in params and "range_str" in params:
        _raw = str(params.get("range_str", ""))
        if _raw.startswith("{"):
            import re as _re
            # 尝试从破损 JSON 中提取 path
            _pm = _re.search(r'"path"\s*:\s*"([^"]*)"', _raw)
            if _pm:
                params["path"] = _pm.group(1)
            # 提取 content：从 "content": " 到文件末尾（JSON 可能被截断，取到最后一个 "） )
            _cm = _re.search(r'"content"\s*:\s*"(.+)', _raw, _re.DOTALL)
            if _cm:
                _raw_content = _cm.group(1)
                # 去掉末尾可能多出的 `"}` 残留
                _raw_content = _raw_content.rstrip('"').rstrip('}').rstrip('"').rstrip('}')
                # 反转义（单遍左到右，正确处理字面量 \\n）
                _raw_content = _unescape_json_fragment(_raw_content)
                params["content"] = _raw_content
                params.pop("range_str", None)
                if _pm and not _raw_content.endswith("\n"):
                    params["content"] += "\n"
                _mcp_debug(f"write_file 容错: path={params.get('path', '?')}, content_len={len(params.get('content', ''))}")

    # ── 统一权限门控（前置：内置与 MCP 工具一视同仁，杜绝内置 handler 绕过）──
    # 从 build_native_tools() 查找当前工具的权限级别
    # 2026-09 修复：MCP 工具在工具表中注册为 mcp_<name>（见 build_native_tools），
    # 而 raw_tool 已剥离 mcp_ 前缀——此前用裸名查表永远 miss → 全部落 PERM_READONLY，
    # DangerFullAccess 形同虚设。现同时匹配裸名与 mcp_ 前缀名。
    _tool_permission = PERM_READONLY  # 默认只读安全
    try:
        _tool_permission = _get_tool_permission(raw_tool)
        if _tool_permission == PERM_READONLY:
            # MCP 工具注册为 mcp_<name>，裸名 miss 时再查前缀名
            _tool_permission = _get_tool_permission(f"mcp_{raw_tool}")
    except Exception:
        pass

    # ── Agent 工具分级：explore/plan 完全只读 → 自动放行（等同 ReadOnly）──
    # lint/test/web_search_agent 可经安全管线跑命令/联网 → 保持 DangerFullAccess（显式批准）
    if raw_tool == "Agent":
        _agent_type = str((params or {}).get("type", "explore")).lower()
        if _agent_type in ("explore", "plan"):
            _tool_permission = PERM_READONLY

    if _tool_permission == PERM_DANGER_FULL:
        # 2026-09：所有危险权限工具一律自动放行（开发者工具，影响可控）。
        # 不再弹 y/N 确认；仍创建审批令牌留痕，供审计。
        try:
            _scope = ApprovalScope(action=raw_tool, policy="dangerous_write")
            _token_grant = _APPROVAL_LEDGER.create(
                scope=_scope, approving_actor="auto",
                approved_executor="ai", max_uses=1, ttl_seconds=60,
            )
            console.print(_mcp_t(f"  [dim]✓ 自动授权（令牌: {_token_grant.token[:12]}...）[/]",
                                f"  [dim]✓ Auto-authorized (token: {_token_grant.token[:12]}...)[/]"))
        except Exception:
            pass
    # WorkspaceWrite / ReadOnly：全模式自动放行
    # （可逆操作：UndoLastEdit + git + 终端实时可见性提供安全；确认弹窗留给不可逆动作）

    # ---- 路径安全校验（所有工具执行前必须经过 Onyx 沙箱检查） ----
    if path_validator is not None:
        arguments = dict(params) if params else {}
        file_tool_paths = _extract_paths_from_tool(raw_tool, arguments)
        for p in file_tool_paths:
            ok, err_msg = path_validator(raw_tool, p)
            if not ok:
                return False, err_msg

    # ── 内置分析工具（不经过 MCP，直接 Python 执行；已通过上述统一门禁）──
    if raw_tool in _BUILTIN_HANDLERS:
        try:
            result = _BUILTIN_HANDLERS[raw_tool](params or {})
            # AI 虚拟沙盒：文件类工具输出中的物理路径 → 虚拟路径（隐藏真实 cwd）
            if raw_tool in AI_FILE_TOOLS and sandbox.is_active():
                result = sandbox.display_text(result)
            return True, result
        except Exception as e:
            return False, f"Builtin tool error: {e}"

    # ── 未知名兜底路由闸门（2026-09 修复 C3）──
    # 裸名既不在内置 handler、也不在注册表 MCP 工具、又不在只读白名单时，
    # 拒绝直达 filesystem——防模型幻觉输出 delete_file 等被剔除的破坏性名字
    # 直接落 MCP 执行（此前无任何确认）。
    if raw_tool not in _BUILTIN_HANDLERS:
        _known_mcp_tool = False
        try:
            _reg = get_registry()
            for _srv in _reg.server_names():
                if _reg.get(f"mcp__{_srv}__{raw_tool}"):
                    _known_mcp_tool = True
                    break
        except Exception:
            pass
        if not _known_mcp_tool and raw_tool not in _MCP_BARE_READONLY_TOOLS:
            return False, (
                f"未知工具 `{tool_name}`：工具表中不存在该名称，已拒绝路由到 MCP。"
                f"破坏性文件操作请使用工具列表中注册的 mcp_ 前缀工具名。"
            )

    proc = connect_mcp_server(name, user_home_dir)
    if proc is None:
        return False, f"MCP server '{name}' not connected"

    # 构建 MCP call_tool arguments
    arguments = dict(params) if params else {}

    # edit_file: old_string/new_string → MCP edits[].oldText/.newText
    if raw_tool == "edit_file":
        old_str = arguments.pop("old_string", None) or arguments.pop("old_str", None)
        new_str = arguments.pop("new_string", None) or arguments.pop("new_str", None)
        if old_str is not None:
            arguments["edits"] = [{"oldText": old_str, "newText": new_str or ""}]
        # 移除旧的 range_str/operation（兼容旧格式）
        arguments.pop("range_str", None)
        arguments.pop("operation", None)

    call_params = {
        "name": raw_tool,
        "arguments": arguments,
    }

    result = _mcp_request(proc, "tools/call", call_params, msg_id=int(time.time() * 1000) % 1000000)

    if result is None:
        return False, "MCP tool call timeout"

    if "error" in result:
        return False, f"MCP error: {result['error']}"

    # 提取 content
    content = result.get("result", {}).get("content", [])
    if isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(item.get("text", ""))
            elif isinstance(item, str):
                text_parts.append(item)
        output = "\n".join(text_parts)
    elif isinstance(content, str):
        output = content
    else:
        output = str(content)

    # AI 虚拟沙盒：文件类 MCP 工具输出中的物理路径 → 虚拟路径（隐藏真实 cwd）
    if raw_tool in AI_FILE_TOOLS and sandbox.is_active():
        output = sandbox.display_text(output)

    return True, output


def _extract_paths_from_tool(tool_name: str, arguments: Dict) -> List[str]:
    """从 MCP 工具参数中提取所有文件路径，用于安全校验"""
    paths = []
    # 常见的路径参数名
    path_keys = {"path", "paths", "source", "destination", "file_path",
                 "directory", "dir_path", "target", "file", "dir"}

    for key in path_keys:
        val = arguments.get(key)
        if isinstance(val, str) and val:
            paths.append(val)

    # edit_file 特殊处理：edits 中可能含路径引用
    if tool_name == "edit_file":
        edits = arguments.get("edits", [])
        if isinstance(edits, list):
            for edit in edits:
                if isinstance(edit, dict):
                    for k in path_keys:
                        v = edit.get(k)
                        if isinstance(v, str) and v:
                            paths.append(v)

    return paths


# ── AI 虚拟沙盒：涉及 path 参数的文件类工具白名单 ──
# 这些工具的路径参数在执行前会经 sandbox.resolve() 做虚拟→物理转换（越界拦截），
# 输出中的物理路径再由各 _exec_* 经 sandbox.display() 反向映射回虚拟路径。
AI_FILE_TOOLS = frozenset({
    # ── 内置文件工具 ──
    "validate_edit", "preview_edit", "get_file_info", "read_file",
    "write_file", "edit_file", "glob_search", "grep_search",
    "search_file", "ListDirectory", "DirectoryTree",
    # ── 代码分析工具（bin/ai_lib/tools/code_analysis.py）──
    "py_diagnostics", "py_symbols", "LspDiagnostics", "LspSymbols",
    "LspHover", "LspDefinition", "LspReferences", "LspCompletion", "LspFormat",
    # ── Git 工具 ──
    "GitStatus", "GitDiff", "GitLog", "GitBranch",
    # ── MCP filesystem 工具（走外部 server，参数同样先转物理）──
    "read_text_file", "read_multiple_files", "read_media_file",
    "list_directory", "directory_tree", "list_directory_with_sizes",
    "search_files", "search_content", "create_directory",
    "move_file", "copy_file", "delete_file", "delete_directory",
    "list_allowed_directories", "get_workspace_folders",
})

# ── 裸名直达 MCP filesystem 的只读白名单（2026-09 修复 C3）──
# 破坏性 filesystem 工具（delete_file/move_file/copy_file/create_directory 等）
# 已从可见工具表剔除，且不允许以裸名直达——模型幻觉输出这些名字时直接拒绝，
# 必须走 mcp_ 前缀（挂有权限门禁 + 沙盒校验）才能调用。
_MCP_BARE_READONLY_TOOLS = frozenset({
    "read_text_file", "read_multiple_files", "read_media_file",
    "list_directory", "directory_tree", "list_directory_with_sizes",
    "search_files", "search_content",
    "list_allowed_directories", "get_workspace_folders",
})


def _parse_mcp_tool_name(full_name: str) -> tuple:
    """解析 mcp__server__tool → (server, tool_name)"""
    if full_name.startswith("mcp__"):
        parts = full_name.split("__", 2)
        if len(parts) == 3:
            return parts[1], parts[2]
    return "filesystem", full_name


def _display_tool_params(params_str: str) -> str:
    """从工具调用原始参数中提取关键路径/参数用于面板展示（路径绝不截断）。

    优先展示 path/pattern/uuid 等关键字段的完整值；无法解析时回退为原始参数
    （限制长度并显式省略，避免把 write_file 的完整 content 刷进面板）。
    """
    try:
        if params_str.strip().startswith("{"):
            data = json.loads(params_str)
            for key in ("path", "pattern", "uuid", "task_id", "cron_id",
                        "team_id", "query", "url", "name"):
                val = data.get(key)
                if val:
                    return f"{key}={val}"
    except Exception:
        pass
    if len(params_str) <= 200:
        return params_str
    return params_str[:200] + "…"


def _fix_unquoted_json_values(s: str) -> str:
    """修复模型常见的「字符串值未加引号」JSON 错误。

    例: {"query": deepseek 推理 api} → {"query": "deepseek 推理 api"}
    只处理不含嵌套结构/逗号的简单标量值；布尔/null/数字保持原样。
    """
    _pattern = re.compile(
        r'("(?:\\.|[^"\\])*"\s*:\s*)([^"\[\]{},][^\[\]{},]*?)(\s*[,}])'
    )

    def _rep(m: "re.Match") -> str:
        _val = m.group(2).strip()
        if not _val:
            return m.group(0)
        if _val.lower() in ("true", "false", "null", "none"):
            return m.group(1) + ("null" if _val.lower() == "none" else _val.lower()) + m.group(3)
        try:
            float(_val)
            return m.group(0)  # 数字保留原样
        except ValueError:
            pass
        return m.group(1) + '"' + _val.replace("\\", "\\\\").replace('"', '\\"') + '"' + m.group(3)

    return _pattern.sub(_rep, s)


def _json_loads_lenient(text: str) -> Optional[Dict]:
    """宽松 JSON 解析：修复模型常见的 JSON 格式错误后尝试解析。

    四级容错：
      1. json.loads 直接解析；
      2. ast.literal_eval（容忍单引号、True/False/None）；
      3. 正则修复未加引号的字符串值后重试（_fix_unquoted_json_values）；
      4. 从文本中提取 JSON 子串（如 `web_search({...})` 外壳文本）。
    成功返回 dict，全部失败返回 None。
    """
    if not isinstance(text, str) or not text.strip():
        return None
    _s = text.strip()
    # 1) 直接解析
    try:
        _v = json.loads(_s)
        return _v if isinstance(_v, dict) else None
    except Exception:
        pass
    # 2) ast.literal_eval（单引号 / True / False / None）
    try:
        import ast as _ast
        _v = _ast.literal_eval(_s)
        return _v if isinstance(_v, dict) else None
    except Exception:
        pass
    # 3) 正则修复未加引号的值
    try:
        _fixed = _fix_unquoted_json_values(_s)
        if _fixed != _s:
            _v = json.loads(_fixed)
            return _v if isinstance(_v, dict) else None
    except Exception:
        pass
    # 4) 提取 JSON 子串（容忍外壳文本）
    try:
        _m = re.search(r"\{.*\}", _s, re.DOTALL)
        if _m:
            _v = json.loads(_m.group(0))
            return _v if isinstance(_v, dict) else None
    except Exception:
        pass
    return None


def _parse_tool_params(params_str: str, body: str) -> Dict:
    """
    解析工具参数：JSON 优先，回退到旧空格分隔格式。
    - 新格式: params_str 是 JSON，直接解析
    - 兼容: body 是 JSON（放在块体中）
    - 旧格式: "path 10-30" 空格分隔
    """
    # 尝试 JSON
    for candidate in (params_str, body):
        if candidate and candidate.strip().startswith("{"):
            try:
                return json.loads(candidate.strip())
            except (json.JSONDecodeError, ValueError) as _je:
                _mcp_debug(f"_parse_tool_params JSON decode failed: {_je}")
                # 宽松解析兜底：修复「字符串值未加引号」等模型常见坏格式
                _loose = _json_loads_lenient(candidate)
                if _loose is not None:
                    return _loose
                pass

    # 回退：旧空格分隔格式 "path [operation] [range]"
    params = {"range_str": params_str, "content": body}
    if params_str and not params_str.startswith("{"):
        parts = params_str.split(None, 1)
        params["path"] = parts[0]
        if len(parts) > 1:
            rest = parts[1]
            if rest in ("replace", "insert", "delete", "append"):
                params["operation"] = rest
            else:
                params["range_str"] = rest
    return params


def list_mcp_servers() -> str:
    """列出已注册的 MCP 服务器及状态"""
    config = _load_mcp_config()
    servers = config.get("servers", {})
    if not servers:
        return _mcp_t("没有已注册的 MCP 服务器", "No MCP servers registered")

    lines = ["📋 MCP 服务器列表:", ""]
    for sname, sinfo in servers.items():
        installed = "✅" if sinfo.get("installed") else "❌"
        running = "🟢" if is_mcp_server_running(sname) else "⚫"
        desc = sinfo.get("description", "")
        lines.append(f"  {running} {installed} {sname}: {desc}")
    return "\n".join(lines)


def install_mcp_server_cmd(name: str, package: str = None) -> str:
    """
    安装并注册一个 MCP 服务器
    ai -mcp install <name> [package]
    默认 package = @modelcontextprotocol/server-<name>
    """
    if package is None:
        package = f"@modelcontextprotocol/server-{name}"

    console.print(_mcp_t(f"📦 正在安装 {package}...", f"📦 Installing {package}..."), style="cyan")

    # 构建 env（Termux 上需重定向到内部存储，避免 FUSE symlink 错误）
    env = os.environ.copy()
    try:
        from lib.get_lib_path import _is_termux_environment
        if _is_termux_environment():
            from lib.get_lib_path import TERMUX_PREFIX, TERMUX_HOME
            termux_cache = os.path.join(TERMUX_PREFIX, "tmp", "npm_cache")

            # 彻底删除整个 npm cache（包括 _cacache 和 _npx）
            if os.path.exists(termux_cache):
                try:
                    shutil.rmtree(termux_cache)
                except Exception:
                    pass
            _ensure_dir(termux_cache)

            env["NPM_CONFIG_CACHE"] = termux_cache
            env["npm_config_cache"] = termux_cache
            env["HOME"] = TERMUX_HOME
            console.print(f"📱 Termux: npm cache → {termux_cache}", style="dim")
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["npm", "install", "-g", package],
            capture_output=True, text=True, timeout=120,
            env=env
        )
        if result.returncode != 0:
            return _mcp_t(f"❌ 安装失败: {result.stderr[:300]}", f"❌ Install failed: {result.stderr[:300]}")
    except FileNotFoundError:
        return _mcp_t("❌ npm 未找到，请先安装 Node.js", "❌ npm not found, please install Node.js")
    except subprocess.TimeoutExpired:
        return _mcp_t("❌ 安装超时（120s）", "❌ Install timed out (120s)")

    # 注册到配置文件
    config = _load_mcp_config()
    config.setdefault("servers", {})[name] = {
        "name": name,
        "description": f"MCP server: {package}",
        "command": "npx",
        "args": ["-y", package, "/"],
        "auto_start": False,
        "installed": True,
    }
    _save_mcp_config(config)

    return f"✅ MCP server '{name}' 安装并注册成功\n   包: {package}\n   使用 ai -mcp list 查看状态"


def remove_mcp_server_cmd(name: str) -> str:
    """从注册表移除 MCP 服务器"""
    if name == "filesystem":
        return "❌ 默认 filesystem MCP server 不可移除"

    # 先关闭进程
    if name in MCP_SERVER_PROCESSES:
        proc = MCP_SERVER_PROCESSES.pop(name)
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        _cleanup_mcp_stderr_buffers(proc.pid)

    MCP_TOOLS_CACHE.pop(name, None)

    config = _load_mcp_config()
    if name in config.get("servers", {}):
        del config["servers"][name]
        _save_mcp_config(config)
        return f"✅ MCP server '{name}' 已移除"
    return f"⚠️ MCP server '{name}' 未在注册表中"


def handle_mcp_command(subcommand: str, args: List[str]) -> None:
    """
    处理 ai -mcp <subcommand> 子命令
    在 handle_ai 入口处调用
    """
    if subcommand == "list":
        result = list_mcp_servers()
        console.print(result, style="white")
    elif subcommand == "install":
        mcp_name = args[0] if args else None
        mcp_pkg = args[1] if len(args) > 1 else None
        if not mcp_name:
            console.print(_mcp_t("用法: ai -mcp install <name> [package]", "Usage: ai -mcp install <name> [package]"), style="bold yellow")
            return
        result = install_mcp_server_cmd(mcp_name, mcp_pkg)
        console.print(result, style="white")
    elif subcommand == "remove":
        mcp_name = args[0] if args else None
        if not mcp_name:
            console.print(_mcp_t("用法: ai -mcp remove <name>", "Usage: ai -mcp remove <name>"), style="bold yellow")
            return
        result = remove_mcp_server_cmd(mcp_name)
        console.print(result, style="white")
    else:
        console.print(
            "用法: ai -mcp <install|list|remove> [args]",
            style="bold yellow"
        )

