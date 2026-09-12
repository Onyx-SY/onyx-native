# -*- coding: utf-8 -*-
"""
memory_tools.py — Onyx 记忆工具执行器（从 bin/ai_cmd.py 提取）

MemoryRead / MemorySearch / remember / forget / memory / compact_stats
的工具实现，以及记忆根（global/project）解析与查询缓存。

依赖自给：_i18n（.i18n）、_run_grep_lines（grep_utils）；
storage / number_lines 保持延迟导入（与 ai_cmd 原行为一致）。
"""
import os
import re
import json
import time
from collections import OrderedDict
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


# ── 记忆查询缓存（LRU；读缓存带文件指纹、搜索缓存带 TTL）──
_MEMORY_QUERY_CACHE: "OrderedDict[str, tuple]" = OrderedDict()
_MEMORY_CACHE_MAX = 50
_MEMORY_SEARCH_TTL = 30.0  # 搜索缓存有效期（秒）


def _file_fingerprint(file_path: str):
    """文件指纹 (mtime_ns, size)；取不到返回 None。"""
    try:
        st = os.stat(file_path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _cache_lookup(key: str):
    """命中则刷新 LRU 顺序并返回条目 (kind, stamp, result)，否则 None。"""
    entry = _MEMORY_QUERY_CACHE.get(key)
    if entry is None:
        return None
    _MEMORY_QUERY_CACHE.move_to_end(key)
    return entry


def _cache_fresh(entry, fp=None) -> bool:
    """条目是否有效：'fp' 比对文件指纹；'ttl' 比对过期时间。"""
    kind, stamp, _res = entry
    if kind == "fp":
        return stamp is not None and stamp == fp
    if kind == "ttl":
        return time.time() < stamp
    return False


def _cache_store(key: str, kind: str, stamp, result: str) -> str:
    """写入缓存（真 LRU：超限淘汰最久未用）。"""
    _MEMORY_QUERY_CACHE[key] = (kind, stamp, result)
    _MEMORY_QUERY_CACHE.move_to_end(key)
    while len(_MEMORY_QUERY_CACHE) > _MEMORY_CACHE_MAX:
        _MEMORY_QUERY_CACHE.popitem(last=False)
    return result


def _cache_query(key: str, result: str) -> str:
    """兼容旧接口：按 TTL 写入缓存并返回结果。"""
    return _cache_store(key, "ttl", time.time() + _MEMORY_SEARCH_TTL, result)


def _candidate_bases() -> list:
    """返回所有已知记忆根下的 `.ai_s` 基目录（按优先级、去重）。

    第 1 个 = 当前会话记忆根（handle_ai 注入）；
    若当前根形如 `<X>/.ai_s/projects/<id>`，再补上祖先 `<X>/.ai_s`（global 根），
    使 project 模式也能读到全局记忆。
    """
    home = get_memory_home()
    bases: list = []
    cur = os.path.normpath(os.path.join(home, ".ai_s"))
    bases.append(cur)
    parts = cur.split(os.sep)
    for i, seg in enumerate(parts):
        if seg == ".ai_s" and i + 1 < len(parts) and parts[i + 1] == "projects":
            gbase = os.sep.join(parts[:i + 1])
            if gbase and gbase not in bases:
                bases.append(gbase)
            break
    return bases


def _path_in_base(path: str, base: str) -> str:
    """在指定记忆根 base 下，把简写 path 展开为具体文件路径。"""
    if path.startswith("chat/"):
        name = path[5:]
        if name.endswith(".json"):
            name = name[:-5]
        return os.path.join(base, "chat", name + ".json")
    if path.startswith("library/"):
        uuid_part = path[8:]
        if uuid_part.endswith(".txt"):
            uuid_part = uuid_part[:-4]
        return os.path.join(base, "library", uuid_part + ".txt")
    if path in ("onyx_ai", "onyx_ai.md"):
        return os.path.join(base, "onyx_ai.md")
    if os.path.isabs(path):
        return path
    return os.path.join(base, path)


def _inside_any_root(p: str, allowed) -> bool:
    """p（abspath / realpath 任一形态）是否落在任一允许根内。"""
    forms = {os.path.abspath(p), os.path.realpath(p)}
    for a in allowed:
        for c in forms:
            if c == a or c.startswith(a + os.sep):
                return True
    return False


def _resolve_memory_path(path: str) -> str:
    """将记忆路径简写解析为完整文件路径（支持多记忆根）。

    接受格式:
      library/<uuid>       → <root>/.ai_s/library/<uuid>.txt
      library/<uuid>.txt   → <root>/.ai_s/library/<uuid>.txt  (兼容旧格式)
      chat/<name>          → <root>/.ai_s/chat/<name>.json
      onyx_ai              → <root>/.ai_s/onyx_ai.md
    依次在 _candidate_bases() 的各个记忆根中查找，返回**首个存在者**；
    都不存在时返回首个合法候选（供 not-found 报错定位）。

    边界守卫：任何路径（含 ../ 穿越与绝对路径）必须落在**任一记忆根**内，
    越界抛 ValueError（防任意文件读取）。
    """
    bases = _candidate_bases()
    allowed = []
    for b in bases:
        allowed.append(os.path.abspath(b))
        allowed.append(os.path.realpath(b))

    candidates = [os.path.normpath(_path_in_base(path, b)) for b in bases]
    # 1) 首个"不越界且存在"的候选
    for cand in candidates:
        if _inside_any_root(cand, allowed) and os.path.exists(cand):
            return cand
    # 2) 都不存在：返回首个不越界候选（not-found 报错定位用）
    for cand in candidates:
        if _inside_any_root(cand, allowed):
            return cand
    # 3) 全部越界 → 拒绝
    raise ValueError(f"⛔ 记忆路径越界: '{path}' 不在任何记忆根内（{', '.join(bases)}）")


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


_READ_BUDGET = 28000  # 单次返回的字符预算（超出则自截断并给续读指针）
_RANGE_RE_LINE = re.compile(r"^\s*(\d+)\s*$")
_RANGE_RE_SPAN = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*$")


def _parse_range(range_str: str):
    """严格解析 range：'N' 或 'A-B'。非法抛 ValueError（不再静默降级为全文）。"""
    s = (range_str or "").strip()
    m = _RANGE_RE_SPAN.match(s)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if a < 1 or a > b:
            raise ValueError(f"bad span: {s}")
        return a, b
    m = _RANGE_RE_LINE.match(s)
    if m:
        n = int(m.group(1))
        if n < 1:
            raise ValueError(f"bad line: {s}")
        return n, n
    raise ValueError(f"bad range: {s}")


def _other_root_hint(path: str) -> str:
    """未命中时，若其它记忆根存在同名文件，附上提示（帮助定位跨根记忆）。"""
    try:
        for b in _candidate_bases():
            cand = os.path.normpath(_path_in_base(path, b))
            if os.path.exists(cand):
                return "\n" + _i18n("mem_other_root_hint", "bilingual", path=cand)
    except Exception:
        pass
    return ""


def _exec_memory_read(path: str, range_str: str = None) -> str:
    """读取记忆文件，支持行号范围。返回带行号前缀的内容。

    - range 严格校验（非法明确报错，不再静默返回全文）
    - 流式逐行读取（不全量 read + split）
    - 超过字符预算时自截断，并给出「续读 range」指针
    """
    try:
        file_path = _resolve_memory_path(path)
        if not os.path.exists(file_path):
            return _i18n("mem_read_not_found", "bilingual", path=path,
                         file_path=file_path) + _other_root_hint(path)

        fp = _file_fingerprint(file_path)
        cache_key = f"read:{file_path}:{range_str or 'full'}"
        entry = _cache_lookup(cache_key)
        if entry and _cache_fresh(entry, fp):
            return entry[2] + "\n\n" + _i18n("cached_hint", "bilingual")

        if range_str:
            try:
                start, end = _parse_range(range_str)
            except ValueError:
                return _i18n("mem_read_bad_range", "bilingual", value=range_str)
        else:
            start, end = 1, None

        from lib.native_fs.panels import number_lines as _num_lines
        selected: list = []
        total_lines = 0
        used = 0
        shown_end = start - 1
        truncated = False
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, 1):
                total_lines = i
                if i < start or (end is not None and i > end):
                    continue
                if truncated:
                    continue
                ln = line[:-1] if line.endswith("\n") else line
                if used + len(ln) + 1 > _READ_BUDGET and selected:
                    truncated = True
                    continue
                selected.append(ln)
                used += len(ln) + 1
                shown_end = i

        if not selected and total_lines > 0 and start > total_lines:
            return _i18n("mem_read_out_of_range", "bilingual", start=start, total=total_lines)

        view_mode = "full" if not range_str else (f"line {start}" if start == end else f"range {start}-{end}")
        raw = "\n".join(selected)
        numbered = _num_lines(raw, start=start) if selected else ""
        header = f"📄 `{path}` " + _i18n("mem_read_header", "bilingual", mode=view_mode, total=total_lines)
        result = f"{header}\n\n{numbered}"
        if truncated:
            next_end = shown_end + 500
            result += "\n\n" + _i18n("mem_read_more", "bilingual",
                                     shown=f"{start}-{shown_end}", total=total_lines,
                                     nxt=f"{shown_end + 1}-{next_end}")
        return _cache_store(cache_key, "fp", fp, result)
    except Exception as e:
        return _i18n("mem_read_failed", "bilingual", err=e)


def _exec_memory_search(pattern: str, uuid: str = "all", context: int = 3,
                        case_insensitive: bool = True, scope: str = "all") -> str:
    """在记忆文件中搜索关键字。

    uuid 参数：真实 UUID → 只搜对应 library/<uuid>.txt（跨记忆根查找）；
               'all'（默认）→ 按 scope 在语义范围内查找。
    scope 参数（uuid='all' 时生效）：
               'all'（默认）→ library/ + chat/ + onyx_ai.md
               'library'     → 仅会话归档 library/
               'chat'        → 仅对话 chat/
    注意：'all' 只搜上述记忆内容，**不含** tmp/（AI 工作文件）、time/、projects/。
    本质是文件搜索：复用 grep 文件搜索逻辑（_run_grep_lines），结果带行号。
    """
    try:
        bases = _candidate_bases()
        scope = (scope or "all").strip().lower()
        if scope not in ("all", "library", "chat"):
            scope = "all"

        # ── 解析 uuid → 搜索目标 ──
        if uuid and uuid != "all":
            uuid_part = uuid
            if uuid_part.startswith("library/"):
                uuid_part = uuid_part[8:]
            if uuid_part.endswith(".txt"):
                uuid_part = uuid_part[:-4]
            _bad = ("/" in uuid_part or "\\" in uuid_part
                    or uuid_part in ("", ".", "..") or os.path.isabs(uuid_part))
            if _bad:
                return _i18n("mem_search_uuid_missing", "bilingual", uuid=uuid,
                             path=os.path.join(bases[0], "library", uuid_part + ".txt"))
            file_path = None
            for b in bases:
                cand = os.path.join(b, "library", uuid_part + ".txt")
                if os.path.exists(cand):
                    file_path = cand
                    break
            if file_path is None:
                return _i18n("mem_search_uuid_missing", "bilingual", uuid=uuid,
                             path=os.path.join(bases[0], "library", uuid_part + ".txt"))
            search_targets = [file_path]
            scope_label = uuid_part
        else:
            want = {"all": ("library", "chat", "onyx_ai.md"),
                    "library": ("library",),
                    "chat": ("chat",)}[scope]
            search_targets = []
            for b in bases:
                for item in want:
                    p = os.path.join(b, item)
                    if os.path.exists(p) and p not in search_targets:
                        search_targets.append(p)
            if not search_targets:
                return _i18n("mem_search_dir_missing", "bilingual", path=bases[0])
            scope_label = scope

        cache_key = f"search:{pattern}:{scope_label}:{context}:{case_insensitive}"
        entry = _cache_lookup(cache_key)
        if entry and _cache_fresh(entry):
            return entry[2] + "\n\n" + _i18n("cached_hint", "bilingual")

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
        return _cache_store(cache_key, "ttl", time.time() + _MEMORY_SEARCH_TTL,
                            f"{header}\n\n{formatted}")
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
