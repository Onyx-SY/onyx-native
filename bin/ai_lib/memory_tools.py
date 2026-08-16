# -*- coding: utf-8 -*-
"""
memory_tools.py — Onyx 记忆工具执行器（从 bin/ai_cmd.py 提取）

MemoryRead / MemorySearch / remember / forget / memory / compact_stats
的工具实现，以及记忆根（global/project）解析与查询缓存。

依赖自给：_i18n（.i18n）、_run_grep_lines（grep_utils）；
storage / number_lines 保持延迟导入（与 ai_cmd 原行为一致）。
"""
import os
import json
from typing import Callable, Optional

from .i18n import _ as _i18n  # 双语文本（中英）
from .grep_utils import _run_grep_lines


# ── 模块级记忆根（由 handle_ai 注入 _mem_home：MemoryRead/MemorySearch 等
#    路径解析跟随记忆模式 global/project，未注入时回落用户主目录）──
_MEM_HOME = None


def set_memory_home(home_dir: str) -> None:
    """注入当前会话记忆根目录（handle_ai 内 _mem_home）。"""
    global _MEM_HOME
    _MEM_HOME = home_dir


def get_memory_home() -> str:
    """返回当前记忆根目录；未注入时回落用户主目录（兼容旧调用）。"""
    return _MEM_HOME or os.path.expanduser("~")


# ── 记忆查询缓存（避免重复查询）──
_MEMORY_QUERY_CACHE: dict[str, str] = {}
_MEMORY_CACHE_MAX = 50


def _cache_query(key: str, result: str) -> str:
    """缓存查询结果。"""
    global _MEMORY_QUERY_CACHE
    if len(_MEMORY_QUERY_CACHE) >= _MEMORY_CACHE_MAX:
        # 淘汰最旧的
        old_key = next(iter(_MEMORY_QUERY_CACHE))
        _MEMORY_QUERY_CACHE.pop(old_key, None)
    _MEMORY_QUERY_CACHE[key] = result
    return result


def _resolve_memory_path(path: str) -> str:
    """将记忆路径简写解析为完整文件路径。

    接受格式:
      library/<uuid>       → ~/.ai_s/library/<uuid>.txt
      library/<uuid>.txt   → ~/.ai_s/library/<uuid>.txt  (兼容旧格式)
      chat/<name>          → ~/.ai_s/chat/<name>.json
      onyx_ai              → ~/.ai_s/onyx_ai.md
    记忆根跟随 get_memory_home()（project 模式 → ~/.ai_s/projects/<id>/）

    边界守卫：任何路径（含 ../ 穿越与绝对路径）必须落在记忆根内，
    越界抛 ValueError（防任意文件读取）。
    """
    home = get_memory_home()
    base = os.path.join(home, ".ai_s")
    if path.startswith("chat/"):
        name = path[5:]
        if name.endswith(".json"):
            name = name[:-5]
        _cand = os.path.join(base, "chat", name + ".json")
    elif path.startswith("library/"):
        uuid_part = path[8:]
        if uuid_part.endswith(".txt"):
            uuid_part = uuid_part[:-4]
        _cand = os.path.join(base, "library", uuid_part + ".txt")
    elif path == "onyx_ai" or path == "onyx_ai.md":
        _cand = os.path.join(base, "onyx_ai.md")
    elif os.path.isabs(path):
        _cand = path
    else:
        _cand = os.path.join(base, path)

    # ── 边界守卫：realpath 后必须在记忆根内，否则拒绝 ──
    _base_real = os.path.realpath(base)
    _norm = os.path.normpath(_cand)
    _p_real = os.path.realpath(_norm) if os.path.exists(_norm) else os.path.abspath(_norm)
    if _p_real == _base_real or _p_real.startswith(_base_real + os.sep):
        return _norm
    raise ValueError(f"⛔ 记忆路径越界: '{path}' 不在记忆根 {base} 内")


def _get_file_uuid(file_path: str) -> str:
    """从记忆文件路径提取 UUID。"""
    base = os.path.basename(file_path)
    name, ext = os.path.splitext(base)
    if ext == ".txt":
        return name  # library 文件：文件名就是 UUID
    elif ext == ".json":
        # chat 文件：尝试提取 session_uuid
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for m in data.get("messages", []):
                suuid = m.get("session_uuid", "")
                if suuid:
                    return suuid
        except Exception:
            pass
        return f"chat/{name}"
    return base


def _exec_memory_read(path: str, range_str: str = None) -> str:
    """读取记忆文件，支持行号范围。返回带行号前缀的内容。"""
    try:
        file_path = _resolve_memory_path(path)
        if not os.path.exists(file_path):
            return _i18n("mem_read_not_found", "bilingual", path=path, file_path=file_path)

        # 检查缓存
        cache_key = f"read:{file_path}:{range_str or 'full'}"
        if cache_key in _MEMORY_QUERY_CACHE:
            return _MEMORY_QUERY_CACHE[cache_key] + "\n\n" + _i18n("cached_hint", "bilingual")

        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        all_lines = content.split("\n")
        total_lines = len(all_lines)
        start_line = 1
        view_mode = "full"

        if range_str:
            try:
                if "-" in range_str:
                    start, end = map(int, range_str.split("-", 1))
                    start_line = max(1, start)
                    end_line = min(total_lines, end)
                    selected = all_lines[start_line - 1:end_line]
                    view_mode = f"range {start_line}-{end_line}"
                else:
                    line_no = int(range_str)
                    start_line = max(1, min(line_no, total_lines))
                    selected = [all_lines[start_line - 1]]
                    view_mode = f"line {start_line}"
            except (ValueError, IndexError):
                selected = all_lines
        else:
            selected = all_lines

        # ── 添加行号（与 read_file 一致）──
        from lib.native_fs.panels import number_lines as _num_lines
        raw = "\n".join(selected)
        numbered = _num_lines(raw, start=start_line)

        # 不在此处截断 — AI 显式调用 MemoryRead 需要完整内容。
        # 上层 _MAX_TOOL_OUTPUT (32KB) 统一切断，保证不撑爆上下文。
        header = f"📄 `{path}` " + _i18n("mem_read_header", "bilingual", mode=view_mode, total=total_lines)
        result = f"{header}\n\n{numbered}"
        return _cache_query(cache_key, result)
    except Exception as e:
        return _i18n("mem_read_failed", "bilingual", err=e)


def _exec_memory_search(pattern: str, uuid: str = "all", context: int = 3,
                        case_insensitive: bool = True) -> str:
    """在记忆文件中搜索关键字。

    uuid 参数：真实 UUID → 只搜 ~/.ai_s/library/<uuid>.txt；
              'all'（默认）→ 全范围查找（chat/ + library/ + onyx_ai.md）。
    本质是文件搜索：复用 grep 文件搜索逻辑（_run_grep_lines），结果带行号
    （file:line:content）。
    """
    try:
        home = get_memory_home()
        base = os.path.join(home, ".ai_s")

        # ── 解析 uuid → 搜索目标 ──
        scope_label = uuid or "all"
        if uuid and uuid != "all":
            uuid_part = uuid
            if uuid_part.startswith("library/"):
                uuid_part = uuid_part[8:]
            if uuid_part.endswith(".txt"):
                uuid_part = uuid_part[:-4]
            file_path = os.path.join(base, "library", uuid_part + ".txt")
            # ── 边界守卫：uuid 含 ../ 或绝对路径时拒绝（防穿越）──
            _base_real = os.path.realpath(base)
            _fp_real = os.path.realpath(file_path) if os.path.exists(file_path) else os.path.abspath(file_path)
            if not (_fp_real == _base_real or _fp_real.startswith(_base_real + os.sep)):
                return _i18n("mem_search_uuid_missing", "bilingual", uuid=uuid, path=file_path)
            if not os.path.exists(file_path):
                return _i18n("mem_search_uuid_missing", "bilingual", uuid=uuid, path=file_path)
            search_targets = [file_path]
        else:
            if not os.path.isdir(base):
                return _i18n("mem_search_dir_missing", "bilingual", path=base)
            search_targets = [base]
            scope_label = "all"

        cache_key = f"search:{pattern}:{scope_label}:{context}:{case_insensitive}"
        if cache_key in _MEMORY_QUERY_CACHE:
            return _MEMORY_QUERY_CACHE[cache_key] + "\n\n" + _i18n("cached_hint", "bilingual")

        # ── 复用文件搜索逻辑（grep -rn，结果含行号）──
        raw = _run_grep_lines(pattern, search_targets, context=context,
                              case_insensitive=case_insensitive, timeout=30)
        if raw is None:
            return _i18n("mem_search_timeout", "bilingual")
        if not raw.strip():
            return _i18n("mem_search_no_match", "bilingual", pattern=pattern)

        # 按文件分组 + UUID 标注（保留 file:line 行号信息）
        groups: dict[str, list[str]] = {}
        file_order: list[str] = []
        current_file = None
        current_block: list[str] = []

        def _flush_block():
            nonlocal current_file, current_block
            if current_file and current_block:
                if current_file not in groups:
                    groups[current_file] = []
                    file_order.append(current_file)
                groups[current_file].extend(current_block)
            current_block = []

        for line in raw.split("\n"):
            if line == "--":
                _flush_block()
                current_file = None
                continue
            if not line:
                continue
            idx = line.find(":")
            if idx <= 0:
                current_block.append(line)
                continue
            maybe_path = line[:idx]
            rest = line[idx + 1:]
            idx2 = rest.find(":")
            if idx2 <= 0:
                current_block.append(line)
                continue
            maybe_lineno = rest[:idx2]
            if not maybe_lineno.isdigit():
                current_block.append(line)
                continue
            if maybe_path != current_file:
                _flush_block()
                current_file = maybe_path
            current_block.append(line)

        _flush_block()

        out = []
        first = True
        for fpath in file_order:
            lines = groups[fpath]
            uuid_label = _get_file_uuid(fpath)
            if not first:
                out.append("─" * 40)
            first = False
            out.append(f"📌 UUID: `{uuid_label}`")
            out.append(f"   {_i18n('mem_search_path', 'bilingual')}: {fpath}")
            if fpath.endswith(".txt") or (fpath.endswith(".json") and not uuid_label.startswith("chat/")):
                out.append(f"   💡 {_i18n('mem_search_hint', 'bilingual', uuid=uuid_label)}")
            out.append("")
            out.extend(lines)
            out.append("")

        formatted = "\n".join(out)
        if len(formatted) > 20000:
            formatted = formatted[:20000] + "\n\n" + _i18n("mem_search_truncated", "bilingual")

        header = _i18n("mem_search_header", "bilingual", pattern=pattern,
                       scope=scope_label, ctx=context, files=len(groups))
        return _cache_query(cache_key, f"{header}\n\n{formatted}")
    except Exception as e:
        return _i18n("mem_search_failed", "bilingual", err=e)


def _exec_remember_session(session_id: str) -> str:
    """标记 library 会话为重要"""
    try:
        from .storage import mark_session_important
        home_dir = get_memory_home()
        return mark_session_important(home_dir, session_id)
    except Exception as e:
        return f"❌ remember failed: {e}"


def _exec_forget_session(session_id: str) -> str:
    """归档 library 会话"""
    try:
        from .storage import archive_session
        home_dir = get_memory_home()
        return archive_session(home_dir, session_id)
    except Exception as e:
        return f"❌ forget failed: {e}"


def _exec_search_library(query: str, limit: int = 8) -> str:
    """BM25 搜索海马体"""
    try:
        from .storage import search_library
        home_dir = get_memory_home()
        return search_library(home_dir, query, limit)
    except Exception as e:
        return f"❌ memory search failed: {e}"


def _exec_list_hippocampus(filter_type: str = None, limit: int = 30) -> str:
    """列出海马体活跃记忆"""
    try:
        from .storage import list_hippocampus
        home_dir = get_memory_home()
        return list_hippocampus(home_dir, filter_type=filter_type, limit=limit)
    except Exception as e:
        return f"❌ memory list failed: {e}"


def _exec_read_memory(session_id: str) -> str:
    """用 UUID 直接读取 library 完整记录"""
    try:
        from .storage import load_memory_by_uuid
        home_dir = get_memory_home()
        content = load_memory_by_uuid(home_dir, session_id)
        if not content:
            return f"Session {session_id} not found in library."
        # 限制长度防止上下文溢出
        if len(content) > 8000:
            content = content[:8000] + f"\n\n... (truncated, {len(content)} chars total)"
        return content
    except Exception as e:
        return f"❌ memory read failed: {e}"


def _exec_compact_stats() -> str:
    """查看压缩状态"""
    try:
        from .storage import get_compaction_stats
        home_dir = get_memory_home()
        return get_compaction_stats(home_dir)
    except Exception as e:
        return f"❌ compact_stats failed: {e}"


def _exec_list_timeline(day: str = "", month: str = "", year: str = "",
                        start: str = "", end: str = "", skill: str = "") -> str:
    """时间线查询：按日/月/年/区间查看任务与摘要（memory list 二级参数）。

    day   = '2026-2-12'  → 当日任务列表（list.json）
    month = '2026-6'     → 该月每日描述（timeline.json）
    year  = '2026'       → 该年每月描述（timeline.json）
    start/end = '2026-6-7','2026-6-8' → 区间逐日任务列表
    skill = '<name>'     → 读取技能文档：'onyx' 读 etc/ai/onyx.md，否则读 .onyx/skills/<name>.md
    """
    try:
        # ── skill 参数：读取技能/介绍文档（按需查看，不占系统前缀）──
        if skill:
            return _exec_read_skill(skill)
        from .timeline import list_timeline
        home_dir = get_memory_home()
        return list_timeline(home_dir, day=day, month=month, year=year,
                             start=start, end=end)
    except Exception as e:
        return f"❌ memory list timeline failed: {e}"


def _exec_read_skill(name: str) -> str:
    """读取技能文档：'onyx' → etc/ai/onyx.md；其它 → .onyx/skills/<name>.md。"""
    name = (name or "").strip()
    if not name:
        return "❌ skill 参数为空：memory list skill=<name>（onyx 或 skills 目录下的技能名）"
    candidates = []
    try:
        from .config import ROOT_DIR
        if name.lower() in ("onyx", "introduction", "intro"):
            candidates = [
                os.path.join(ROOT_DIR, "onyx", "etc", "ai", "onyx.md"),
                os.path.join("etc", "ai", "onyx.md"),
            ]
        else:
            _safe = "".join(c for c in name if c.isalnum() or c in "-_")
            candidates = [
                os.path.join(ROOT_DIR, "onyx", ".onyx", "skills", f"{_safe}.md"),
                os.path.join(".onyx", "skills", f"{_safe}.md"),
                os.path.join(ROOT_DIR, "onyx", ".onyx", "skills", f"{_safe}", "SKILL.md"),
                os.path.join(".onyx", "skills", f"{_safe}", "SKILL.md"),
            ]
    except Exception:
        pass
    for _p in candidates:
        try:
            if os.path.exists(_p):
                with open(_p, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                return f"📘 技能文档 [{name}]\n\n" + content[:8000]
        except Exception:
            continue
    return f"❌ 未找到技能文档: {name}（可查 etc/ai/onyx.md 或 .onyx/skills/ 目录）"
