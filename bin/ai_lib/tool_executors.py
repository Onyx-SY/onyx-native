# -*- coding: utf-8 -*-
"""
tool_executors.py — 内置工具执行器（_exec_* 系列：文件/搜索/技能/任务/团队/定时/Git 等）

从 bin/ai_cmd.py 拆分（模块化架构重构）：
- 文件编辑 /storage/emulated/0/abPython/PythonProject/工具/Hacker--V1.00.1/src/Hacker/onyx-test read/write/edit /storage/emulated/0/abPython/PythonProject/工具/Hacker--V1.00.1/src/Hacker/onyx-test glob/grep/search /storage/emulated/0/abPython/PythonProject/工具/Hacker--V1.00.1/src/Hacker/onyx-test Skill /storage/emulated/0/abPython/PythonProject/工具/Hacker--V1.00.1/src/Hacker/onyx-test Todo /storage/emulated/0/abPython/PythonProject/工具/Hacker--V1.00.1/src/Hacker/onyx-test Task /storage/emulated/0/abPython/PythonProject/工具/Hacker--V1.00.1/src/Hacker/onyx-test Team /storage/emulated/0/abPython/PythonProject/工具/Hacker--V1.00.1/src/Hacker/onyx-test Cron /storage/emulated/0/abPython/PythonProject/工具/Hacker--V1.00.1/src/Hacker/onyx-test Undo /storage/emulated/0/abPython/PythonProject/工具/Hacker--V1.00.1/src/Hacker/onyx-test Git；
- 全局注册表（_TASK_REGISTRY/_TEAM_REGISTRY/_CRON_REGISTRY/_LAST_EDIT/_AI_INTERRUPTED）
  在函数体内延迟导入，避免模块级循环导入（共享同一对象，无状态分叉）。
"""

import os
import re
import json
import subprocess
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from lib.task_system import TaskPacket, TaskResource, TaskScope
from rich.console import Console

from .config import _render_edit_diff
from .grep_utils import _run_grep_lines
from .i18n import _ as _i18n
from .native_tools import build_native_tools


console = Console()


def _exec_validate_edit(file_path: str, search: str, replace: str) -> str:
    """校验 SEARCH/REPLACE 编辑。"""
    try:
        from lib.edit_engine import validate_edit, dry_run_edit
        ok, msg = validate_edit(file_path, search, replace)
        if ok:
            diff = dry_run_edit(file_path, search, replace)
            return _i18n("validate_ok", "bilingual") + f"\n\n{diff[:2000]}"
        return f"❌ {msg}"
    except Exception as e:
        return _i18n("validate_failed", "bilingual", err=e)


def _exec_preview_edit(file_path: str, search: str, replace: str) -> str:
    """预览 diff。"""
    try:
        from lib.edit_engine import dry_run_edit
        diff = dry_run_edit(file_path, search, replace)
        if diff.startswith("❌"):
            return diff
        return f"```diff\n{diff}\n```"
    except Exception as e:
        return _i18n("preview_failed", "bilingual", err=e)


def _exec_get_file_info(file_path: str) -> str:
    """获取文件基本信息。"""
    try:
        import os, datetime
        if not os.path.exists(file_path):
            return _i18n("finfo_not_found", "bilingual", path=file_path)
        stat = os.stat(file_path)
        size = stat.st_size
        mtime = datetime.datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
        # 行数
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                line_count = sum(1 for _ in f)
        except Exception:
            line_count = -1
        size_str = f"{size:,} bytes"
        if size > 1024:
            size_str += f" ({size/1024:.1f} KB)"
        if size > 1024*1024:
            size_str += f" ({size/1024/1024:.1f} MB)"
        # 文件类型
        _, ext = os.path.splitext(file_path)
        ext = ext.lower() if ext else "(no extension)"
        return (
            f"📄 {file_path}\n"
            f"  {_i18n('finfo_size', 'bilingual')}: {size_str}\n"
            f"  {_i18n('finfo_mtime', 'bilingual')}: {mtime}\n"
            f"  {_i18n('finfo_lines', 'bilingual')}: {line_count if line_count >= 0 else 'binary/unknown'}\n"
            f"  {_i18n('finfo_type', 'bilingual')}: {ext}"
        )
    except Exception as e:
        return _i18n("finfo_failed", "bilingual", err=e)


# ── read_file 大纲模式（大文件自动折叠）──
READ_OUTLINE_THRESHOLD = 64 * 1024          # 超过 64 KiB 自动切大纲模式
READ_OUTLINE_HEAD = 80                      # 大纲模式返回前 N 行（方向感）

# 通用语言顶层定义扫描（Python 走 ast，其他语言用此正则兜底）
_SYMBOL_DEF_RE = re.compile(
    r"^\s*(?:(?:export|default|public|private|protected|static|abstract|"
    r"final|async|internal|extern|pub|global)\s+)*"
    r"(?:def\s+|class\s+|func\s+|function\s+|fn\s+|interface\s+|"
    r"struct\s+|enum\s+|trait\s+|type\s+)"
    r"[A-Za-z_][A-Za-z0-9_]*"
)


def _fmt_read_size(num_bytes: int) -> str:
    """人类可读文件大小，如 311.8 KiB"""
    b = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if b < 1024 or unit == "TiB":
            return f"{b:.0f} {unit}" if unit == "B" else f"{b:.1f} {unit}"
        b /= 1024
    return f"{num_bytes} B"


def _build_symbol_outline(file_path: str, lines: List[str], total_lines: int) -> str:
    """提取顶层函数/类符号大纲（带行号，无数量上限）。Python 用 ast，其余语言正则兜底。"""
    width = len(str(total_lines))
    out: List[str] = []

    if os.path.splitext(file_path)[1].lower() == ".py":
        try:
            import ast as _ast
            tree = _ast.parse("\n".join(lines))
            for node in tree.body:
                if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
                    if isinstance(node, _ast.ClassDef):
                        head = f"class {node.name}"
                    else:
                        args = [a.arg for a in node.args.args[:6]]
                        if node.args.vararg:
                            args.append("*" + node.args.vararg.arg)
                        if node.args.kwarg:
                            args.append("**" + node.args.kwarg.arg)
                        prefix = "async def" if isinstance(node, _ast.AsyncFunctionDef) else "def"
                        head = f"{prefix} {node.name}({', '.join(args)})"
                    out.append(f"{node.lineno:>{width}}  │ {head}")
        except Exception:
            out = []  # 语法错误等 → 回退正则

    if not out:
        for i, line in enumerate(lines, 1):
            if _SYMBOL_DEF_RE.match(line):
                out.append(f"{i:>{width}}  │ {line.strip()[:120]}")
    return "\n".join(out)


def _exec_read_file(file_path: str, range_str: str = None, head: int = None, tail: int = None) -> str:
    """
    读取文件内容，支持行号范围 range / head / tail。
    超过 64 KiB 的大文件默认返回大纲模式（文件大小 + 前 80 行 + 符号大纲 + 钻取提示），
    避免整文件灌入上下文；需要细节时用 range / head / tail / grep_search 钻取。
    
    返回带行号前缀的内容（每行格式 "LINE │ 内容"），
    AI 可以精确引用行号而无需重读文件。
    同时记录到 library 时保留完整路径+行号+内容。
    """
    from ..ai_cmd import _AI_INTERRUPTED
    try:
        abs_path = os.path.abspath(file_path)
        if not os.path.exists(abs_path):
            return _i18n("read_not_found", "bilingual", path=abs_path)
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            # 大文件分块读取，支持 Ctrl+C 中断
            f.seek(0, 2)
            file_size = f.tell()
            f.seek(0)
            if file_size < 1024 * 1024 * 4:  # 4MB 以下直接读
                content = f.read()
            else:
                parts = []
                while True:
                    if _AI_INTERRUPTED:
                        return "⏹ 用户中断"
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    parts.append(chunk)
                content = "".join(parts)
        
        lines = content.split("\n")
        total_lines = len(lines)
        
        # ── 行号范围处理 ──
        start_line = 1
        end_line = total_lines
        view_mode = "full"
        
        if range_str:
            try:
                if "-" in range_str:
                    start, end = map(int, range_str.split("-", 1))
                    start_line = max(1, start)
                    end_line = min(total_lines, end)
                    selected = lines[start_line - 1:end_line]
                    view_mode = f"range {start_line}-{end_line}"
                else:
                    line_no = int(range_str)
                    start_line = max(1, min(line_no, total_lines))
                    end_line = start_line
                    selected = [lines[start_line - 1]]
                    view_mode = f"line {start_line}"
            except (ValueError, IndexError):
                selected = lines
        elif head:
            n = max(1, min(int(head), total_lines))
            start_line, end_line = 1, n
            selected = lines[:n]
            view_mode = f"head {n}"
        elif tail:
            n = max(1, min(int(tail), total_lines))
            start_line = max(1, total_lines - n + 1)
            end_line = total_lines
            selected = lines[start_line - 1:]
            view_mode = f"tail {n}"
        elif file_size > READ_OUTLINE_THRESHOLD:
            # ── 大纲模式：大小 + 前 N 行 + 符号大纲 + 钻取提示 ──
            from lib.native_fs.panels import number_lines as _num_lines
            preview = _num_lines("\n".join(lines[:READ_OUTLINE_HEAD]), start=1)
            symbols = _build_symbol_outline(abs_path, lines, total_lines)
            count = symbols.count("\n") + 1 if symbols else 0
            return (
                f"📖 `{abs_path}` "
                + _i18n("read_outline_header", "bilingual",
                        total=total_lines, size=_fmt_read_size(file_size))
                + "\n\n" + _i18n("read_outline_preview", "bilingual", n=READ_OUTLINE_HEAD)
                + "\n" + preview
                + "\n\n" + _i18n("read_outline_symbols", "bilingual", count=count)
                + "\n" + (symbols or _i18n("read_outline_none", "bilingual"))
                + "\n\n" + _i18n("read_outline_hint", "bilingual")
            )
        else:
            selected = lines
        
        # ── 构建带行号前缀的输出 ──
        from lib.native_fs.panels import number_lines as _num_lines
        raw_selected = "\n".join(selected)
        numbered = _num_lines(raw_selected, start=start_line)
        
        # 构建返回文本：路径 + 行范围 + 行号内容
        header = (
            f"📖 `{abs_path}` "
            + _i18n("read_header", "bilingual", mode=view_mode, total=total_lines)
        )
        
        # 限制输出大小：最多 8000 字符
        if len(numbered) > 8000:
            numbered = numbered[:8000] + f"\n... (truncated, {len(numbered)} chars total)"
        
        return f"{header}\n\n{numbered}"
    except Exception as e:
        return _i18n("read_failed", "bilingual", err=e)


def _exec_write_file(file_path: str, content: str) -> str:
    """写入文件（全量覆盖）。返回中包含 original_file 供撤销。"""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
        old_content = ""
        is_update = False
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                old_content = f.read()
            if old_content == content:
                return _i18n("write_unchanged", "bilingual", path=file_path)
            is_update = True
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        total_lines = content.count("\n") + (1 if content else 0)
        # 保存到全局撤销记录
        global _LAST_EDIT
        if is_update:
            _LAST_EDIT = {"path": file_path, "original": old_content, "action": "write"}
        else:
            _LAST_EDIT = {"path": file_path, "original": "", "action": "write"}
        return json.dumps({
            "result": _i18n("write_ok", "bilingual", path=file_path, lines=total_lines),
            "original_file": old_content if is_update else None,
            "file_path": file_path,
        }, ensure_ascii=False)
    except Exception as e:
        return f"❌ write_file failed: {e}"


def _exec_edit_file(file_path: str, old_string: str, new_string: str) -> str:
    """SEARCH/REPLACE 精确替换。返回中包含 original_file 供撤销。"""
    try:
        from lib.edit_engine import apply_edit
        # 读旧内容做 diff 预览 + 保存原始内容
        old_content = ""
        try:
            if os.path.exists(file_path):
                with open(file_path, "r", encoding="utf-8", errors="replace") as _f:
                    old_content = _f.read()
        except Exception:
            old_content = ""
        if old_content and old_string in old_content:
            new_content = old_content.replace(old_string, new_string, 1)
            console.print("  " + _i18n("edit_console_ok", "bilingual", path=file_path))
            try:
                _render_edit_diff(old_content, new_content)
            except Exception:
                pass
        ok, msg = apply_edit(file_path, old_string, new_string)
        if ok:
            # 保存到全局撤销记录
            global _LAST_EDIT
            _LAST_EDIT = {"path": file_path, "original": old_content, "action": "edit"}
            return json.dumps({
                "result": _i18n("edit_ok", "bilingual", path=file_path),
                "original_file": old_content,
                "file_path": file_path,
            }, ensure_ascii=False)
        _err_lower = msg.lower()
        if "not found" in _err_lower or "not unique" in _err_lower:
            return f"❌ {msg}\n" + _i18n("edit_hint", "bilingual")
        return f"❌ {msg}"
    except Exception as e:
        return _i18n("edit_failed", "bilingual", err=e)





# ──────────────────── 新增工具执行器 ────────────────────

def _exec_glob_search(pattern: str, path: str = None) -> str:
    """使用 glob 模式查找文件。"""
    try:
        import glob as _glob
        search_root = path or "."
        matches = _glob.glob(pattern, root_dir=search_root, recursive=True) if hasattr(_glob, 'root_dir') else []
        if not matches:
            try:
                import subprocess as _sp
                if "**" in pattern:
                    result = _sp.run(["find", search_root, "-type", "f", "-name", pattern.split("/")[-1]],
                                     capture_output=True, text=True, timeout=5)
                    matches = [l.strip() for l in result.stdout.split("\n") if l.strip()]
                else:
                    result = _sp.run(["ls", "-1", os.path.join(search_root, pattern)],
                                     capture_output=True, text=True, timeout=5, shell=True)
                    matches = [l.strip() for l in result.stdout.split("\n") if l.strip()]
            except Exception:
                pass
        if not matches:
            return _i18n("glob_no_match", "bilingual", pattern=pattern, root=search_root)
        # 限制返回数量
        total = len(matches)
        if total > 200:
            matches = matches[:200]
            return "\n".join(matches) + _i18n("glob_more", "bilingual", extra=total - 200, total=total)
        return "\n".join(matches)
    except Exception as e:
        return _i18n("glob_failed", "bilingual", err=e)



def _exec_grep_search(pattern: str, path: str = None, glob: str = None,
                      context: int = 0, i: bool = False, head_limit: int = None) -> str:
    """使用正则表达式搜索文件内容。支持上下文行、大小写控制。"""
    try:
        search_root = path or "."
        output = _run_grep_lines(pattern, search_root, context=context,
                                 case_insensitive=i, glob=glob, timeout=15)
        if output is None:
            return _i18n("grep_timeout", "bilingual")
        if not output.strip():
            return _i18n("grep_no_match", "bilingual", pattern=pattern, root=search_root)
        lines = output.split("\n")
        if head_limit and len(lines) > head_limit:
            output = "\n".join(lines[:head_limit]) + _i18n("grep_head_limited", "bilingual", total=len(lines), shown=head_limit)
        if len(output) > 10000:
            output = output[:5000] + _i18n("grep_out_truncated", "bilingual", total=len(output))
        return output
    except Exception as e:
        return _i18n("grep_failed", "bilingual", err=e)


def _exec_search_file(pattern: str, path: str = None) -> str:
    """按文件名关键字递归查找文件（自动跳过依赖/构建目录），返回完整路径列表。"""
    try:
        import fnmatch as _fnmatch
        root = os.path.abspath(path) if path else os.getcwd()
        if not os.path.isdir(root):
            return _i18n("sf_root_missing", "bilingual", path=root)
        skip_dirs = {".git", "node_modules", "__pycache__", "dist", "build",
                     ".venv", "venv", "target", "out", ".cache", ".next", ".nuxt"}
        matches: list = []
        is_glob = any(ch in pattern for ch in "*?[")
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in skip_dirs]
            for fname in filenames:
                if is_glob:
                    ok = _fnmatch.fnmatch(fname.lower(), pattern.lower())
                else:
                    ok = pattern.lower() in fname.lower()
                if ok:
                    matches.append(os.path.join(dirpath, fname))
                    if len(matches) >= 1000:
                        break
            if len(matches) >= 1000:
                break
        if not matches:
            return _i18n("sf_no_match", "bilingual", pattern=pattern, root=root)
        matches.sort()
        total = len(matches)
        if total > 200:
            shown = matches[:200]
            return "\n".join(shown) + _i18n("sf_more", "bilingual", extra=total - 200, total=total)
        return "\n".join(matches)
    except Exception as e:
        return _i18n("sf_failed", "bilingual", err=e)


def _exec_tool_search(query: str) -> str:
    """搜索可用工具。"""
    try:
        # 获取当前注册的工具列表
        from bin.ai_cmd import build_native_tools
        import inspect
        tools = build_native_tools()
        query_lower = query.lower()
        matches = []
        for t in tools:
            func = t.get("function", {})
            name = func.get("name", "")
            desc = func.get("description", "")
            if query_lower in name.lower() or query_lower in desc.lower():
                perm = t.get("x_permission", "Unknown")
                matches.append(f"- `{name}` [{perm}]\n  {desc[:120]}")
        if not matches:
            # 返回所有工具列表供参考
            all_tools = []
            for t in tools:
                func = t.get("function", {})
                name = func.get("name", "")
                perm = t.get("x_permission", "?")
                all_tools.append(f"- `{name}` [{perm}]")
            return f"未找到与 '{query}' 相关的工具。可用工具列表:\n" + "\n".join(all_tools)
        return f"找到 {len(matches)} 个相关工具:\n\n" + "\n\n".join(matches)
    except Exception as e:
        return f"❌ ToolSearch failed: {e}"


def _find_skill_file(skill_name: str) -> Tuple[Optional[str], str]:
    """在所有标准位置查找 SKILL.md 文件。
    
    查找路径（按优先级）:
      1. .onyx/skills/<name>/SKILL.md        ← Onyx 原生
      2. .onyx/commands/<name>.md
      3. .claude/skills/<name>/SKILL.md
      4. .claude/commands/<name>.md
      5. ~/.onyx/skills/<name>/SKILL.md
      6. ~/.claude/skills/<name>/SKILL.md
      7. .reasonix/skills/<name>/SKILL.md
      8. <name>.md (当前目录)
      9. skills/<name>.md (当前目录)
    """
    import glob as _glob
    _cwd = os.getcwd()
    _home = os.path.expanduser("~")

    _search_roots = [
        # ═══ Onyx 原生（最高优先级）═══
        os.path.join(_cwd, ".onyx", "skills"),
        os.path.join(_cwd, ".onyx", "commands"),
        # ═══ 第三方技能目录兼容（.claude 等）═══
        os.path.join(_cwd, ".claude", "skills"),
        os.path.join(_cwd, ".claude", "commands"),
        # ═══ 其他 ═══
        os.path.join(_cwd, ".reasonix", "skills"),
        os.path.join(_cwd, "skills"),
        # ═══ 用户 Home ═══
        os.path.join(_home, ".onyx", "skills"),
        os.path.join(_home, ".onyx", "commands"),

        os.path.join(_home, ".claude", "skills"),
        os.path.join(_home, ".claude", "commands"),
        os.path.join(_home, ".reasonix", "skills"),
        os.path.join(_home, ".ai_s", "skills"),
    ]

    _found = []

    for root in _search_roots:
        if not os.path.isdir(root):
            continue
        # 精确匹配: <root>/<name>/SKILL.md
        exact = os.path.join(root, skill_name, "SKILL.md")
        if os.path.isfile(exact):
            _found.append((exact, os.path.getmtime(exact)))

        # 精确匹配: <root>/<name>/<name>.md
        exact2 = os.path.join(root, skill_name, f"{skill_name}.md")
        if os.path.isfile(exact2):
            _found.append((exact2, os.path.getmtime(exact2)))

        # 精确匹配: <root>/<name>.md（commands 风格）
        exact3 = os.path.join(root, f"{skill_name}.md")
        if os.path.isfile(exact3):
            _found.append((exact3, os.path.getmtime(exact3)))

        # 精确匹配: <root>/SKILL.md（直接将 root 当作技能目录）
        direct = os.path.join(root, "SKILL.md")
        if os.path.isfile(direct) and os.path.basename(os.path.dirname(direct)).lower() == skill_name.lower():
            _found.append((direct, os.path.getmtime(direct)))

    # 按修改时间排序（最新的优先）
    _found.sort(key=lambda x: x[1], reverse=True)

    if _found:
        return _found[0][0], ""

    # 尝试在当前目录直接查找 <name>.md
    for ext in [".md", ".txt"]:
        _local = os.path.join(os.getcwd(), f"{skill_name}{ext}")
        if os.path.isfile(_local):
            return _local, ""

    # 如果找不到精确匹配，扫描所有 skill 目录做大小写不敏感匹配
    for root in _search_roots:
        if not os.path.isdir(root):
            continue
        try:
            for entry in os.listdir(root):
                entry_path = os.path.join(root, entry)
                if os.path.isdir(entry_path):
                    # 大小写不敏感比较目录名
                    if entry.lower() == skill_name.lower():
                        for _sf in ["SKILL.md", f"{entry}.md"]:
                            _skill_file = os.path.join(entry_path, _sf)
                            if os.path.isfile(_skill_file):
                                return _skill_file, ""
                    # 检查子目录中的 SKILL.md 的 frontmatter name
                    _sf = os.path.join(entry_path, "SKILL.md")
                    if os.path.isfile(_sf):
                        _fm_name = _parse_skill_name_from_file(_sf)
                        if _fm_name and _fm_name.lower() == skill_name.lower():
                            return _sf, ""
                # 也检查根目录下的 .md 文件
                elif entry.lower() == f"{skill_name.lower()}.md":
                    _full = os.path.join(root, entry)
                    if os.path.isfile(_full):
                        return _full, ""
        except PermissionError:
            continue

    return None, f"未找到技能 '{skill_name}'"


def _parse_skill_name_from_file(filepath: str) -> Optional[str]:
    """从 SKILL.md 的 YAML frontmatter 中提取 name 字段。"""
    try:
        with open(filepath, "r", encoding="utf-8") as _f:
            _content = _f.read()
        if _content.startswith("---"):
            _end = _content.find("---", 3)
            if _end > 0:
                _fm = _content[3:_end].strip()
                for _line in _fm.split("\n"):
                    if _line.strip().startswith("name:"):
                        _val = _line.split(":", 1)[1].strip().strip('"').strip("'")
                        if _val:
                            return _val
    except Exception:
        pass
    return None


def _exec_skill(skill: str, args: str = "") -> str:
    """加载并执行技能（Onyx Skill.md 发现系统）。"""
    try:
        skill_path, error = _find_skill_file(skill)
        if not skill_path:
            return f"⚠️ {error}\n\n支持的位置: .onyx/skills/<name>/SKILL.md, .claude/skills/<name>/SKILL.md, ~/.onyx/skills/<name>/SKILL.md"

        with open(skill_path, "r", encoding="utf-8") as _f:
            content = _f.read()

        # 解析 frontmatter
        description = ""
        skill_name = skill
        if content.startswith("---"):
            _end = content.find("---", 3)
            if _end > 0:
                _fm = content[3:_end].strip()
                for _line in _fm.split("\n"):
                    _line = _line.strip()
                    if _line.startswith("name:"):
                        skill_name = _line.split(":", 1)[1].strip().strip('"').strip("'")
                    elif _line.startswith("description:"):
                        description = _line.split(":", 1)[1].strip().strip('"').strip("'")
                # 去掉 frontmatter 后的正文
                body = content[_end + 3:].strip()
        else:
            body = content.strip()
            # 尝试从首行提取 description
            _first_line = body.split("\n")[0] if body else ""
            if _first_line.startswith("description:"):
                description = _first_line.split(":", 1)[1].strip()
                body = "\n".join(body.split("\n")[1:]).strip()

        # 如果传了 args，追加到 body
        if args:
            body += f"\n\n## Arguments\n{args}"

        result_parts = [f"✅ 已加载技能: **{skill_name}**"]
        if description:
            result_parts.append(f"📝 {description}")
        result_parts.append(f"📂 {skill_path}")
        result_parts.append("")
        result_parts.append(body)

        return "\n".join(result_parts)

    except Exception as e:
        return f"❌ Skill '{skill}' 加载失败: {e}"


def _exec_sleep(seconds: int) -> str:
    """等待指定秒数（可被 Ctrl+C 中断）。"""
    try:
        import threading as _threading
        seconds = max(1, min(seconds, 300))  # 限制 1-300 秒
        _threading.Event().wait(seconds)  # 事件驱动可中断等待，替代 time.sleep
        return f"✅ 等待 {seconds} 秒完成"
    except Exception as e:
        return f"❌ Sleep failed: {e}"


def _exec_structured_output(format: str, data: str) -> str:
    """返回结构化数据。"""
    try:
        if format == "json":
            import json as _json
            # 尝试解析 data 是否为合法 JSON
            try:
                parsed = _json.loads(data)
                return _json.dumps(parsed, ensure_ascii=False, indent=2)
            except (_json.JSONDecodeError, ValueError):
                # data 不是 JSON，包装成 JSON
                return _json.dumps({"data": data}, ensure_ascii=False, indent=2)
        return data
    except Exception as e:
        return f"❌ StructuredOutput failed: {e}"


def _exec_todo_write(todos: list) -> str:
    """更新任务列表。"""
    try:
        if not todos:
            return "✅ 任务列表已清空"
        lines = []
        pending = sum(1 for t in todos if t.get("status") == "pending")
        in_progress = sum(1 for t in todos if t.get("status") == "in_progress")
        completed = sum(1 for t in todos if t.get("status") == "completed")
        lines.append(f"📋 任务列表（共 {len(todos)} 项：⏳ {pending} 待办 · 🔄 {in_progress} 进行中 · ✅ {completed} 完成）")
        for t in todos:
            status = t.get("status", "pending")
            content = t.get("content", "")
            active = t.get("activeForm", "")
            icon = {"pending": "⏳", "in_progress": "🔄", "completed": "✅"}.get(status, "⏳")
            status_text = {"pending": "待办", "in_progress": active or "进行中", "completed": "完成"}.get(status, "")
            lines.append(f"{icon} {content} _{status_text}_")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ TodoWrite failed: {e}"


# ═══════════════════════════════════════════════════════════
# Task System — 任务管理器执行器
# ═══════════════════════════════════════════════════════════

def _exec_task_create(prompt: str, description: str = None,
                      scope: str = None, scope_path: str = None,
                      acceptance_criteria: list = None,
                      acceptance_tests: list = None,
                      verification_plan: list = None,
                      resources: list = None,
                      model: str = None, provider: str = None,
                      commit_policy: str = None, branch_policy: str = None,
                      reporting_contract: str = None,
                      escalation_policy: str = None,
                      recovery_policy: str = None) -> str:
    """创建任务。简单模式只传 prompt；高级模式传 TaskPacket 字段。"""
    from ..ai_cmd import _TASK_REGISTRY
    try:
        # 判断是否为高级模式（有 TaskPacket 专属字段）
        if any([scope, acceptance_criteria, acceptance_tests,
                verification_plan, branch_policy, commit_policy,
                reporting_contract, escalation_policy]):
            packet = TaskPacket(
                objective=prompt,
                scope=TaskScope(scope) if scope else TaskScope.WORKSPACE,
                scope_path=scope_path,
                acceptance_criteria=acceptance_criteria or [],
                acceptance_tests=acceptance_tests or [],
                verification_plan=verification_plan or [],
                resources=[TaskResource(**r) if isinstance(r, dict) else r
                           for r in (resources or [])],
                model=model,
                provider=provider,
                commit_policy=commit_policy or "",
                branch_policy=branch_policy or "",
                reporting_contract=reporting_contract or "",
                escalation_policy=escalation_policy or "",
                recovery_policy=recovery_policy,
            )
            task = _TASK_REGISTRY.create_from_packet(packet)
            return (
                f"✅ 任务包已创建: `{task.task_id}`\n"
                f"   目标: {task.prompt}\n"
                f"   范围: {task.description or 'workspace'}\n"
                f"   状态: {task.status.value}"
            )
        else:
            task = _TASK_REGISTRY.create(prompt, description)
            return f"✅ 任务已创建: `{task.task_id}`\n   描述: {task.prompt}"
    except Exception as e:
        return f"❌ TaskCreate 失败: {e}"


def _exec_task_list(status_filter: str = None) -> str:
    """列任务。"""
    from ..ai_cmd import _TASK_REGISTRY
    try:
        tasks = _TASK_REGISTRY.list(status_filter)
        if not tasks:
            return "📭 暂无任务"
        summary = _TASK_REGISTRY.summary()
        lines = [f"📋 任务列表（共 {summary['total']} 项：" +
                 f"🆕 {summary['created']} · 🔄 {summary['running']} · "
                 f"⛔ {summary['blocked']} · ✅ {summary['completed']} · "
                 f"❌ {summary['failed']} · ⏹ {summary['stopped']}）"]
        status_icons = {
            "created": "🆕", "running": "🔄", "blocked": "⛔",
            "completed": "✅", "failed": "❌", "stopped": "⏹",
        }
        for t in tasks:
            icon = status_icons.get(t.status.value, "📌")
            desc = t.description or ""
            lines.append(f"{icon} `{t.task_id}` {t.prompt} _{desc}_")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ TaskList 失败: {e}"


def _exec_task_get(task_id: str) -> str:
    """任务详情。"""
    from ..ai_cmd import _TASK_REGISTRY
    try:
        task = _TASK_REGISTRY.get(task_id)
        if not task:
            return f"❌ 任务未找到: {task_id}"
        lines = [
            f"📌 任务详情: `{task.task_id}`",
            f"   描述: {task.prompt}",
            f"   状态: {task.status.value}",
            f"   说明: {task.description or '-'}",
            f"   创建于: {task.created_at:.1f}",
            f"   更新于: {task.updated_at:.1f}",
        ]
        if task.task_packet:
            p = task.task_packet
            lines.append(f"   范围: {p.scope.value} ({p.scope_path or '-'})")
            lines.append(f"   验收标准: {'; '.join(p.acceptance_criteria) if p.acceptance_criteria else '-'}")
            lines.append(f"   验证计划: {'; '.join(p.verification_plan) if p.verification_plan else '-'}")
        if task.team_id:
            lines.append(f"   团队: {task.team_id}")
        if task.messages:
            lines.append(f"   消息 ({len(task.messages)} 条):")
            for m in task.messages[-5:]:  # 最近 5 条
                lines.append(f"     [{m.role}] {m.content[:80]}")
        if task.output:
            lines.append(f"   输出 ({len(task.output)} 字符)")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ TaskGet 失败: {e}"


def _exec_task_update(task_id: str, status: str = None,
                      message: str = None) -> str:
    """更新任务。"""
    from ..ai_cmd import _TASK_REGISTRY
    try:
        parts = []
        if status:
            _TASK_REGISTRY.set_status(task_id, status)
            parts.append(f"状态 → {status}")
        if message:
            _TASK_REGISTRY.update(task_id, message)
            parts.append("已追加消息")
        if not parts:
            return "⚠️ 未指定更新内容"
        return f"✅ 任务 `{task_id}` 已更新（{'，'.join(parts)}）"
    except KeyError as e:
        return f"❌ {e}"
    except ValueError as e:
        return f"❌ {e}"
    except Exception as e:
        return f"❌ TaskUpdate 失败: {e}"


def _exec_task_stop(task_id: str) -> str:
    """终止任务。"""
    from ..ai_cmd import _TASK_REGISTRY
    try:
        task = _TASK_REGISTRY.stop(task_id)
        return f"⏹ 任务 `{task_id}` 已终止（状态: {task.status.value}）"
    except KeyError as e:
        return f"❌ {e}"
    except ValueError as e:
        return f"❌ {e}"
    except Exception as e:
        return f"❌ TaskStop 失败: {e}"


def _exec_task_board() -> str:
    """看板视图。"""
    from ..ai_cmd import _TASK_REGISTRY
    try:
        board = _TASK_REGISTRY.lane_board()
        lines = [f"📊 任务看板（生成于 {board.generated_at:.1f}）"]
        status_icons = {
            "created": "🆕", "running": "🔄", "blocked": "⛔",
            "completed": "✅", "failed": "❌", "stopped": "⏹",
        }

        lines.append(f"\n── 🔄 Active（{len(board.active)}）──")
        for e in board.active:
            icon = status_icons.get(e.status.value, "📌")
            freshness = f" [{e.freshness.value}]" if e.freshness != "unknown" else ""
            lines.append(f"  {icon} `{e.task_id}` {e.prompt}{freshness}")

        lines.append(f"\n── ⛔ Blocked（{len(board.blocked)}）──")
        for e in board.blocked:
            lines.append(f"  ⛔ `{e.task_id}` {e.prompt}")

        lines.append(f"\n── ✅ Finished（{len(board.finished)}）──")
        for e in board.finished:
            icon = status_icons.get(e.status.value, "📌")
            lines.append(f"  {icon} `{e.task_id}` {e.prompt}")

        if not any([board.active, board.blocked, board.finished]):
            lines.append("\n📭 暂无任务")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ TaskBoard 失败: {e}"


def _exec_task_remove(task_id: str) -> str:
    """删除任务。"""
    from ..ai_cmd import _TASK_REGISTRY
    try:
        task = _TASK_REGISTRY.remove(task_id)
        if task:
            return f"🗑 任务 `{task_id}`（{task.prompt}）已删除"
        return f"❌ 任务未找到: {task_id}"
    except Exception as e:
        return f"❌ TaskRemove 失败: {e}"


# ── 团队管理 ──

def _exec_team_create(name: str, task_ids: list = None) -> str:
    from ..ai_cmd import _TEAM_REGISTRY
    try:
        team = _TEAM_REGISTRY.create(name, task_ids or [])
        return f"✅ 团队已创建: `{team.team_id}`（{team.name}，{len(team.task_ids)} 个任务）"
    except Exception as e:
        return f"❌ TeamCreate 失败: {e}"


def _exec_team_list() -> str:
    from ..ai_cmd import _TEAM_REGISTRY
    try:
        teams = _TEAM_REGISTRY.list()
        if not teams:
            return "📭 暂无团队"
        lines = [f"📋 团队列表（共 {len(teams)} 个）"]
        for t in teams:
            status_icon = {"created": "🆕", "running": "🔄",
                           "completed": "✅", "deleted": "🗑"}.get(t.status.value, "📌")
            lines.append(f"  {status_icon} `{t.team_id}` {t.name}（{len(t.task_ids)} 个任务）")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ TeamList 失败: {e}"


def _exec_team_delete(team_id: str) -> str:
    from ..ai_cmd import _TEAM_REGISTRY
    try:
        team = _TEAM_REGISTRY.delete(team_id)
        return f"🗑 团队 `{team_id}`（{team.name}）已删除"
    except Exception as e:
        return f"❌ TeamDelete 失败: {e}"


# ── 定时任务 ──

def _exec_cron_create(schedule: str, prompt: str, description: str = None) -> str:
    from ..ai_cmd import _CRON_REGISTRY
    try:
        cron = _CRON_REGISTRY.create(schedule, prompt, description)
        return f"✅ 定时任务已创建: `{cron.cron_id}`（{cron.schedule}）"
    except Exception as e:
        return f"❌ CronCreate 失败: {e}"


def _exec_cron_list(enabled_only: bool = False) -> str:
    from ..ai_cmd import _CRON_REGISTRY
    try:
        entries = _CRON_REGISTRY.list(enabled_only)
        if not entries:
            return "📭 暂无定时任务"
        lines = [f"📋 定时任务（共 {len(entries)} 项）"]
        for e in entries:
            status = "✅" if e.enabled else "⏸"
            runs = f"（已执行 {e.run_count} 次）" if e.run_count else ""
            lines.append(f"  {status} `{e.cron_id}` {e.schedule} → {e.prompt} {runs}")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ CronList 失败: {e}"


def _exec_cron_disable(cron_id: str) -> str:
    from ..ai_cmd import _CRON_REGISTRY
    try:
        _CRON_REGISTRY.disable(cron_id)
        return f"⏸ 定时任务 `{cron_id}` 已禁用"
    except Exception as e:
        return f"❌ CronDisable 失败: {e}"


def _exec_cron_delete(cron_id: str) -> str:
    from ..ai_cmd import _CRON_REGISTRY
    try:
        entry = _CRON_REGISTRY.delete(cron_id)
        return f"🗑 定时任务 `{cron_id}`（{entry.prompt}）已删除"
    except Exception as e:
        return f"❌ CronDelete 失败: {e}"


# ═══════════════════════════════════════════════════════════
# LSP — 语言服务器协议执行器
# ═══════════════════════════════════════════════════════════


def _exec_undo_last_edit() -> str:
    """撤销上一次文件编辑或写入操作。"""
    from ..ai_cmd import _LAST_EDIT
    try:
        global _LAST_EDIT
        if not _LAST_EDIT or not _LAST_EDIT.get("path"):
            return "❌ 没有可撤销的编辑记录"
        path = _LAST_EDIT["path"]
        original = _LAST_EDIT["original"]
        action = _LAST_EDIT.get("action", "edit")
        if not original:
            # 新建文件，删除它
            if os.path.exists(path):
                os.remove(path)
                _LAST_EDIT = {}
                return f"🗑 已撤销: 删除新建文件 {path}（原文件不存在）"
            else:
                return f"ℹ️ 文件 {path} 已不存在，无需撤销"
        # 写回原始内容
        with open(path, "w", encoding="utf-8") as f:
            f.write(original)
        _LAST_EDIT = {}
        return f"↩️ 已撤销: {path} 已恢复为修改前的内容"
    except Exception as e:
        return f"❌ UndoLastEdit 失败: {e}"


# ──────────────────── 目录浏览工具执行器 ────────────────────

def _exec_list_directory(path: str = "") -> str:
    """列出目录内容。"""
    try:
        import os
        cwd = path or os.getcwd()
        if not os.path.isdir(cwd):
            return f"❌ 路径不存在或不是目录: {cwd}"
        entries = os.listdir(cwd)
        if not entries:
            return "(空目录)"
        lines = []
        for e in sorted(entries):
            full = os.path.join(cwd, e)
            if os.path.isdir(full):
                lines.append(f"{e}/")
            else:
                lines.append(e)
        return "```\n" + "\n".join(lines) + "\n```"
    except PermissionError:
        return f"❌ 无权限读取目录"
    except Exception as e:
        return f"❌ ListDirectory 错误: {e}"


def _exec_directory_tree(path: str = "", max_depth: int = 2) -> str:
    """递归显示目录树。"""
    try:
        import os
        cwd = path or os.getcwd()
        if not os.path.isdir(cwd):
            return f"❌ 路径不存在或不是目录: {cwd}"
        max_depth = max(1, min(max_depth, 5))
        lines = []
        root_name = os.path.basename(cwd) or cwd
        lines.append(root_name + "/")
        def _walk(dir_path, prefix, depth):
            if depth > max_depth:
                return
            try:
                entries = sorted(os.listdir(dir_path))
            except PermissionError:
                lines.append(prefix + "  [权限不足]")
                return
            for i, e in enumerate(entries):
                is_last = (i == len(entries) - 1)
                connector = "└── " if is_last else "├── "
                full = os.path.join(dir_path, e)
                if os.path.isdir(full):
                    lines.append(prefix + connector + e + "/")
                    sub_prefix = prefix + ("    " if is_last else "│   ")
                    _walk(full, sub_prefix, depth + 1)
                else:
                    lines.append(prefix + connector + e)
        _walk(cwd, "", 1)
        return "```\n" + "\n".join(lines) + "\n```"
    except PermissionError:
        return f"❌ 无权限读取目录"
    except Exception as e:
        return f"❌ DirectoryTree 错误: {e}"


# ──────────────────── Git 工具执行器 ────────────────────

def _exec_git_status(path: str = "") -> str:
    """执行 git status --short。"""
    try:
        import subprocess
        cwd = path or os.getcwd()
        result = subprocess.run(["git", "status", "--short"],
                                capture_output=True, text=True, timeout=10, cwd=cwd)
        if result.returncode != 0:
            return f"❌ git status 失败（可能不是 Git 仓库）:\n{result.stderr.strip()}"
        if not result.stdout.strip():
            return "✅ 工作区干净，无改动"
        files = result.stdout.strip().split("\n")
        summary = f"📊 {len(files)} 个文件已修改\n"
        return summary + "```\n" + result.stdout.strip() + "\n```"
    except FileNotFoundError:
        return "❌ git 未安装"
    except subprocess.TimeoutExpired:
        return "❌ git status 超时"
    except Exception as e:
        return f"❌ git status 错误: {e}"


def _exec_git_diff(path: str = "", staged: bool = False) -> str:
    """执行 git diff。"""
    try:
        import subprocess
        cwd = path or os.getcwd()
        cmd = ["git", "diff", "--no-color"]
        if staged:
            cmd.append("--staged")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10, cwd=cwd)
        if result.returncode != 0:
            return f"❌ git diff 失败:\n{result.stderr.strip()}"
        if not result.stdout.strip():
            return "✅ 无未暂存的变更" if not staged else "✅ 无已暂存的变更"
        output = result.stdout.strip()
        # 截断超大 diff
        if len(output) > 10000:
            output = output[:5000] + f"\n\n…[diff 过长，截断至 5000 字符，共 {len(output)} 字符]…\n\n" + output[-5000:]
        return "```diff\n" + output + "\n```"
    except FileNotFoundError:
        return "❌ git 未安装"
    except subprocess.TimeoutExpired:
        return "❌ git diff 超时"
    except Exception as e:
        return f"❌ git diff 错误: {e}"


def _exec_git_log(path: str = "", count: int = 10) -> str:
    """执行 git log --oneline。"""
    try:
        import subprocess
        cwd = path or os.getcwd()
        count = max(1, min(count, 50))
        result = subprocess.run(
            ["git", "log", f"--max-count={count}", "--oneline", "--decorate"],
            capture_output=True, text=True, timeout=10, cwd=cwd)
        if result.returncode != 0:
            return f"❌ git log 失败（可能不是 Git 仓库）:\n{result.stderr.strip()}"
        if not result.stdout.strip():
            return "ℹ️ 无提交记录"
        return "```\n" + result.stdout.strip() + "\n```"
    except FileNotFoundError:
        return "❌ git 未安装"
    except subprocess.TimeoutExpired:
        return "❌ git log 超时"
    except Exception as e:
        return f"❌ git log 错误: {e}"


def _exec_git_branch(path: str = "") -> str:
    """执行 git branch -a。"""
    try:
        import subprocess
        cwd = path or os.getcwd()
        result = subprocess.run(["git", "branch", "-a"],
                                capture_output=True, text=True, timeout=10, cwd=cwd)
        if result.returncode != 0:
            return f"❌ git branch 失败（可能不是 Git 仓库）:\n{result.stderr.strip()}"
        if not result.stdout.strip():
            return "ℹ️ 无分支信息"
        return "```\n" + result.stdout.strip() + "\n```"
    except FileNotFoundError:
        return "❌ git 未安装"
    except subprocess.TimeoutExpired:
        return "❌ git branch 超时"
    except Exception as e:
        return f"❌ git branch 错误: {e}"

