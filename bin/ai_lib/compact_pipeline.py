# -*- coding: utf-8 -*-
"""
compact_pipeline.py — 对话压缩管道（AutoCompact /storage/emulated/0/abPython/PythonProject/工具/Hacker--V1.00.1/src/Hacker/onyx-test /compact /storage/emulated/0/abPython/PythonProject/工具/Hacker--V1.00.1/src/Hacker/onyx-test 400 超限重试共用）

从 bin/ai_cmd.py 拆分（模块化架构重构）：
- 窗口感知阈值 /storage/emulated/0/abPython/PythonProject/工具/Hacker--V1.00.1/src/Hacker/onyx-test token 估算 /storage/emulated/0/abPython/PythonProject/工具/Hacker--V1.00.1/src/Hacker/onyx-test LLM 保真摘要压缩 /storage/emulated/0/abPython/PythonProject/工具/Hacker--V1.00.1/src/Hacker/onyx-test 压缩摘要落盘 library；
- _reset_ai_interrupt_flags 通过 bin.ai_cmd 模块属性复位中断标志（单一状态源）。
"""

import os
import re
import json
import subprocess
import time
from typing import Dict, List, Optional

from .config import USER_HOME_DIR, load_key_conf
from .memory_compact import (
    summarize_messages, stage1_supersede, get_compact_continuation_message,
    partition_rounds_keep_fold, llm_summarize_messages,
    run_trident_stages, merge_compact_summaries,
    extract_summary_from_compact_message, compress_summary,
    format_compact_summary, estimate_tokens,
)
from .mcp_state import _thread_locals
from .native_tools import get_native_tools_cached
from .storage import get_ai_session_library_dir


def _run_shell_cmd(cmd: str, timeout: int = 10) -> str:
    """执行 shell 命令并返回 stdout 文本。静默失败返回空字符串。"""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True,
                                text=True, timeout=timeout)
        return result.stdout.strip()
    except Exception:
        return ""


# ========================================================================

# -------------------------- 11. handle_ai 核心函数（SSE模式）-------------------------

# ── 对话压缩管道（/compact 与自动压缩共用）──
# 自动压缩阈值：估算 token 数（含 reasoning_content），超过即触发。
# 压缩会重置缓存前缀（一次性 miss），换来后续注意力集中与更长的有效记忆窗口。
_AUTO_COMPACT_TOKEN_THRESHOLD = 600 * 1024

# 工具 schema 的固定 token 开销：校准 tokPerChar 时从真实 prompt tokens 中扣除。
# 回退值 22000（约 55 个内置工具 + 描述）；首次使用时按实际工具 JSON 字节实测。
_TOOL_SCHEMA_TOKEN_OVERHEAD = 22_000
_TOOL_SCHEMA_TOKEN_OVERHEAD_CACHE: Optional[int] = None


def _measured_tool_schema_overhead() -> int:
    """实测当前工具集的 schema 字节开销（估算 token），失败回退 22000。"""
    global _TOOL_SCHEMA_TOKEN_OVERHEAD_CACHE
    if _TOOL_SCHEMA_TOKEN_OVERHEAD_CACHE is not None:
        return _TOOL_SCHEMA_TOKEN_OVERHEAD_CACHE
    try:
        _tools, _ = get_native_tools_cached(USER_HOME_DIR, True)
        _bytes = len(json.dumps(_tools, ensure_ascii=False).encode("utf-8"))
        _TOOL_SCHEMA_TOKEN_OVERHEAD_CACHE = max(int(_bytes / 3.2), 5000)
    except Exception:
        _TOOL_SCHEMA_TOKEN_OVERHEAD_CACHE = _TOOL_SCHEMA_TOKEN_OVERHEAD
    return _TOOL_SCHEMA_TOKEN_OVERHEAD_CACHE

# ── 分层压缩状态 ──
# Layer 2 / TimeBased：闲置超过 60 分钟无交互 → 清理已被 AI 消费的旧工具结果
_IDLE_COMPACT_SECONDS = 60 * 60
_last_ai_interaction_ts = time.time()
# Layer 3 熔断器：连续压缩后仍 ≥90% 阈值达 3 次 → 本会话停止自动压缩，避免反复烧 token
_COMPACT_BREAKER_COUNTS: Dict[str, int] = {}
_COMPACT_BREAKER_DISABLED: Dict[str, bool] = {}

# ── 窗口感知阈值（trigger = 窗口 − 13K 安全缓冲；400 报错实测值可覆盖）──
_WINDOW_SAFETY_BUFFER = 13_000
_SESSION_CONTEXT_WINDOWS: Dict[str, int] = {}


def _persist_compact_to_library(summary: str, saved: int, superseded: int,
                                old_len: int, trident_stats: dict,
                                user_home_dir: str = None, session_id: str = "") -> None:
    """把会话压缩摘要追加到当前 session 的 library 记录（便于人工核对压缩是否失真）。

    2026-09 用户需求：AutoCompact / /compact / 400 超限重试三条路径共用本函数，
    压缩一发生就把摘要 + 统计写进 ~/.ai_s/library/<session_id>.txt。

    防御：session_id 必须为单段文件名（防路径穿越）；任何失败静默，不影响主流程。
    """
    if not summary or not user_home_dir or not session_id:
        return
    if "/" in session_id or "\\" in session_id or session_id in (".", ".."):
        return
    try:
        import datetime as _dt
        from .storage import get_ai_session_library_dir
        from .memory_compact import format_compact_summary
        lib_dir = get_ai_session_library_dir(user_home_dir)
        fpath = os.path.join(lib_dir, f"{session_id}.txt")
        _formatted = format_compact_summary(summary)
        _stats = trident_stats or {}
        _block = (
            f"## 🔄 对话压缩 — {_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"- 压缩范围: {old_len} 条旧消息 → 摘要 + 最近原文（约省 {saved} 条）\n"
            f"- Superseded: {superseded}；Trident: {_stats}\n\n"
            f"{_formatted}\n"
        )
        with open(fpath, "a", encoding="utf-8") as f:
            f.write(f"\n\n---\n\n{_block}")
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        pass


def _compact_conversation_history(conversation_history: List[Dict], keep_last: int = 8,
                                  user_home_dir: str = None, session_id: str = ""):
    """新一代压缩：轮级分区（用户原话/错误轮/旧摘要保留）+ LLM 保真摘要（失败回退正则）。

    管道：
      1. 边界保护：不切断 tool_calls → tool_result 配对
      2. 轮级分区 partition_rounds_keep_fold：keep 原样保留，fold 进摘要
      3. Stage1 Supersede：同文件先 VIEW 后 EDIT/WRITE → 去重过时 VIEW
      4. LLM 分块并行摘要（七段式简报，用户原话不折叠）；失败逐块回退正则
      5. 组装：摘要 system 消息 + kept 原话 + 最近原文

    Returns:
        (new_history, saved_count, superseded_count, old_len, trident_stats)
        new_history 为 None 时表示无可安全压缩的旧消息。
    """
    from .memory_compact import (
        summarize_messages, stage1_supersede, get_compact_continuation_message,
        partition_rounds_keep_fold, llm_summarize_messages,
        run_trident_stages, merge_compact_summaries,
        extract_summary_from_compact_message, compress_summary,
    )
    _total = len(conversation_history)

    # ── Guard: 不切断 tool_calls → tool_result 配对 ──
    # 扫描 ALL tool_calls 块，累积最小安全边界。
    # 任何 tool 结果跨越压缩边界的 tool_calls 块整体保留。
    _min_recent_idx = _total - keep_last
    for _j in range(_total - 1, -1, -1):
        _m = conversation_history[_j]
        if _m.get("tool_calls"):
            _tool_end = _j + 1
            while _tool_end < _total and conversation_history[_tool_end].get("role") == "tool":
                _tool_end += 1
            # 如果此块的 tool 结果触及 _recent，则整块纳入 _recent
            if _tool_end > _min_recent_idx:
                _min_recent_idx = min(_min_recent_idx, _j)
    keep_last = max(keep_last, _total - _min_recent_idx)
    keep_last = min(keep_last, _total - 1)  # 至少保留 1 条在 _old

    _old = conversation_history[:-keep_last] if keep_last < _total else []
    _recent = conversation_history[-keep_last:] if keep_last > 0 else []

    if not _old:
        return None, 0, 0, 0, {}

    # ── 轮级分区：keep（用户原话/错误轮/系统消息/旧摘要）原样保留；
    #    fold（assistant 文本轮/无错误工具轮）进摘要 ──
    _kept, _fold = partition_rounds_keep_fold(_old)

    if not _fold:
        # 无可折叠内容 → 全部原样保留，不压缩
        return None, 0, 0, len(_old), {}

    # ── 提取 kept 中的旧压缩摘要并合并（merge 防嵌套膨胀，单摘要出口）──
    _existing_summaries = []
    _kept_wo_compact = []
    for _m in _kept:
        if _m.get("role") == "system":
            _old_sum = extract_summary_from_compact_message(_m.get("content", ""))
            if _old_sum:
                _existing_summaries.append(_old_sum)
                continue
        _kept_wo_compact.append(_m)
    _kept = _kept_wo_compact

    # ── fold 部分 → entries（Trident 预缩减 + LLM 摘要的输入）──
    _old_entries = []
    for _i, _m in enumerate(_fold):
        _role = _m.get("role", "?")
        _content = _m.get("content", "") or ""
        if not isinstance(_content, str):
            try:
                _content = json.dumps(_content, ensure_ascii=False)
            except Exception:
                _content = str(_content)
        _tc = _m.get("tool_calls")
        _rc = _m.get("reasoning_content", "")
        _body = _content
        if _tc:
            _tc_names = [t.get("function", {}).get("name", "?") for t in _tc]
            try:
                _args = " | ".join(
                    (t.get("function", {}).get("arguments", "") or "")[:300] for t in _tc)
            except Exception:
                _args = ""
            _body = f"[tool_calls: {', '.join(_tc_names)}]{(' ' + _args) if _args else ''}\n{_content}"
        if _rc:
            _body = f"[reasoning]\n{_rc}\n\n{_body}"
        _old_entries.append({
            "session_id": f"turn_{_i}",
            "content": f"### {_role.upper()}\n{_body}",
            "time": "",
        })

    # ── 三段式预缩减：supersede → collapse → cluster ──
    _deduped, _trident_stats = run_trident_stages(_old_entries)
    _superseded = _trident_stats.get("superseded", 0)
    # ── LLM 保真摘要（分块并行；失败自动回退正则）──
    _summary, _used_llm, _chunk_count = llm_summarize_messages(
        _deduped if _deduped else _old_entries,
        user_home_dir=user_home_dir,
        session_id=session_id,
    )
    if not _summary:
        _summary = summarize_messages(_deduped if _deduped else _old_entries)
    if not _summary:
        return None, 0, 0, len(_old), {}

    # ── 合并旧摘要（merge_compact_summaries：展平 prior，追加新内容）──
    for _old_sum in _existing_summaries:
        _summary = merge_compact_summaries(_old_sum, _summary)

    _compact_msg = {
        "role": "system",
        "content": get_compact_continuation_message(_summary),
    }
    _saved = max(0, len(_old) - len(_kept) - 1)
    # ── 压缩摘要持久化到 library（2026-09：便于人工核对压缩是否失真）──
    try:
        _persist_compact_to_library(
            _summary, _saved, _superseded, len(_old), _trident_stats,
            user_home_dir, session_id,
        )
    except Exception:
        pass
    return [_compact_msg] + _kept + _recent, _saved, _superseded, len(_old), _trident_stats


def _is_context_too_long_error(error_str: str) -> bool:
    """检测上下文超限类 API 报错（DeepSeek/OpenAI/Anthropic 常见签名）。"""
    _s = (error_str or "").lower()
    _sigs = (
        "context_length_exceeded",
        "maximum context length",
        "context length exceeded",
        "prompt is too long",
        "too many tokens",
        "context is too long",
        "max context",
    )
    return any(sig in _s for sig in _sigs)


def _parse_context_window_from_error(error_str: str) -> Optional[int]:
    """从 400 报错里解析实测上下文窗口（服务器返回窗口 → 重设触发阈值）。"""
    try:
        _m = re.search(r"maximum context length is (\d+)", error_str, re.IGNORECASE)
        if _m:
            return int(_m.group(1))
        _m = re.search(r"(\d+) tokens[^>]*>?\s*(\d+)\s*maximum", error_str, re.IGNORECASE)
        if _m:
            return max(int(_m.group(1)), int(_m.group(2)))
        _m = re.search(r"(\d+)\s*tokens(?:[^)]{0,40})", error_str, re.IGNORECASE)
        if _m and "context" in error_str.lower():
            return int(_m.group(1))
    except Exception:
        pass
    return None


def _platform_context_window() -> int:
    """按平台取默认上下文窗口（可被 400 报错实测值覆盖）。"""
    try:
        from .config import load_key_conf
        _conf = load_key_conf() or {}
        _plat = _conf.get("platform", "deepseek")
        _map = {"deepseek": 1_000_000, "anthropic": 200_000,
                "openai": 128_000, "custom": 128_000}
        return _map.get(_plat, 1_000_000)
    except Exception:
        return 1_000_000


def _effective_compact_threshold(session_id: str = "") -> int:
    """自动压缩生效阈值 = min(用户 600K, 实测/默认窗口 − 13K 安全缓冲)。"""
    _win = _SESSION_CONTEXT_WINDOWS.get(session_id) or _platform_context_window()
    _thr = min(_AUTO_COMPACT_TOKEN_THRESHOLD, _win - _WINDOW_SAFETY_BUFFER)
    return max(_thr, 32 * 1024)


def _should_append_reply_assistant(ai_txt: str, tool_calls: List) -> bool:
    """纯文本回复是否写入对话历史：正文为空（纯思考轮）不写。

    思考被截断（finish_reason=length）时模型可能只输出 reasoning_content——
    content=None 且无 tool_calls 的 assistant 消息回传会被 API 以
    400 "Invalid assistant message" 拒绝，导致会话卡死。
    """
    return bool(ai_txt and ai_txt.strip()) and not tool_calls


def _estimate_conversation_tokens(conversation_history: List[Dict], session_id: str = "") -> int:
    """估算整段对话历史（含 reasoning_content / tool_calls 参数）的 token 数。

    优先用上一轮真实 usage 校准 tokPerChar（扣除工具 schema 固定开销），
    无历史数据时回退 memory_compact 的 CJK 感知估算。

    session_id: 校准字符数按会话隔离读取。子代理（explore_*）/压缩摘要
                （compact_*）的请求在后台运行，若不隔离，主会话会拿它们的
                字符数做分母 → tokPerChar 虚高 → AutoCompact 提前触发（缓存断裂）。
    """
    from .memory_compact import estimate_tokens
    _total = 0
    _chars = 0
    for _m in conversation_history:
        _c = _m.get("content") or ""
        if not isinstance(_c, str):
            try:
                _c = json.dumps(_c, ensure_ascii=False)
            except Exception:
                _c = str(_c)
        _total += estimate_tokens(_c)
        _chars += len(_c)
        _rc = _m.get("reasoning_content") or ""
        if isinstance(_rc, str) and _rc:
            _total += estimate_tokens(_rc)
            _chars += len(_rc)
        for _tc in _m.get("tool_calls") or []:
            _args = _tc.get("function", {}).get("arguments", "") if isinstance(_tc, dict) else ""
            if isinstance(_args, str) and _args:
                _total += estimate_tokens(_args)
                _chars += len(_args)
    # ── 真实 usage 校准：tokPerChar = (上轮 prompt tokens − 工具 schema 开销) / 上轮字符数 ──
    try:
        _last_prompt = getattr(_thread_locals, "last_prompt_tokens", 0) or 0
        if _last_prompt > 0 and _chars > 0:
            from .api import get_last_request_chars as _glrc
            _last_chars = _glrc(session_id) or 0
            if _last_chars > 0:
                _ratio = (_last_prompt - _measured_tool_schema_overhead()) / _last_chars
                _ratio = min(max(_ratio, 0.10), 0.80)
                return max(int(_chars * _ratio), _total)
    except Exception:
        pass
    return _total


def _reset_ai_interrupt_flags() -> None:
    """复位 AI 中断标志（ai_cmd 与 mcp_state 双份）。

    Ctrl+C 在 SSE 阶段由 _interrupt_handler 置位的是 mcp_state 副本
    （api.py 流式循环检查它），而旧复位点只清 ai_cmd 模块变量——
    mcp_state 标志一旦置位永不复位 → 后续提问 API 一启动就中断。
    """
    # 通过 ai_cmd 模块属性复位（本模块 import 的是值绑定，直接赋值会分叉）
    import bin.ai_cmd as _ac_mod
    _ac_mod._AI_INTERRUPTED = False
    try:
        from . import mcp_state as _msp
        _msp._AI_INTERRUPTED = False
    except Exception:
        pass

