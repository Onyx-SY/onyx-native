"""
edit_engine.py — 编辑验证引擎

对标 Claude Code 的 SEARCH/REPLACE 干运行校验 + 回滚。

能力:
  - validate_edit: 检查 SEARCH 文本存在且唯一
  - dry_run_edit:  输出 unified diff 预览
  - apply_edit:    先备份，应用编辑，失败自动回滚
  - batch_edit:    批量编辑（全部校验成功才执行）

用法:
  from lib.edit_engine import validate_edit, dry_run_edit, apply_edit

  # 校验
  ok, error = validate_edit("file.py", "old text", "new text")

  # 预览 diff
  diff = dry_run_edit("file.py", "old text", "new text")

  # 执行（失败自动回滚）
  ok, error = apply_edit("file.py", "old text", "new text")
"""

import os
import difflib
import tempfile
import shutil
from typing import Tuple, Optional


# ──────────────────────────── 校验 ────────────────────────────

def validate_edit(file_path: str, search: str, replace: str) -> Tuple[bool, str]:
    """
    校验 SEARCH/REPLACE 编辑是否安全。

    返回: (ok: bool, error_message: str)
      ok=True 表示编辑可以安全执行
      error_message 提供具体失败原因
    """
    if not os.path.exists(file_path):
        return False, f"❌ File not found: {file_path}"
    if not os.path.isfile(file_path):
        return False, f"❌ Not a file: {file_path}"
    if not search:
        return False, "❌ SEARCH text is empty"
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        return False, f"❌ Read failed: {e}"

    count = content.count(search)
    if count == 0:
        return False, f"❌ SEARCH text not found in {file_path}"
    if count > 1:
        return False, f"❌ SEARCH text found {count} times (not unique) in {file_path}"

    return True, ""


def _check_search_uniqueness(file_path: str, search: str) -> Tuple[bool, str, Optional[str]]:
    """
    检查 search 存在性+唯一性，返回 (ok, error, content)。
    内部函数，避免重复读文件。
    """
    if not os.path.exists(file_path):
        return False, f"File not found: {file_path}", None
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        return False, f"Read failed: {e}", None
    count = content.count(search)
    if count == 0:
        return False, f"SEARCH text not found in {file_path}", None
    if count > 1:
        return False, f"SEARCH text found {count} times (not unique) in {file_path}", None
    return True, "", content


# ──────────────────────────── Diff 预览 ───────────────────────

def dry_run_edit(file_path: str, search: str, replace: str) -> str:
    """
    生成 SEARCH/REPLACE 编辑的 unified diff 预览。

    返回: diff 文本（适合终端显示）
    """
    ok, error, content = _check_search_uniqueness(file_path, search)
    if not ok:
        return f"❌ {error}"
    new_content = content.replace(search, replace, 1)
    rel_path = os.path.relpath(file_path) if os.path.exists(file_path) else file_path
    diff = difflib.unified_diff(
        content.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=rel_path,
        tofile=rel_path,
    )
    return "".join(diff)


# ──────────────────────────── 执行 + 回滚 ─────────────────────

def _backup_path(file_path: str) -> str:
    """生成备份文件路径。"""
    return file_path + ".bak"


def apply_edit(file_path: str, search: str, replace: str,
               backup: bool = True) -> Tuple[bool, str]:
    """
    执行 SEARCH/REPLACE 编辑，失败自动回滚。

    参数:
      file_path: 目标文件
      search:    要查找的文本
      replace:   替换文本
      backup:    是否创建 .bak 备份

    返回: (ok: bool, message: str)
    """
    # 1. 校验
    ok, error, content = _check_search_uniqueness(file_path, search)
    if not ok:
        return False, error

    # 2. 备份（先创建，以防写入时崩溃）
    bak = _backup_path(file_path)
    if backup:
        try:
            shutil.copy2(file_path, bak)
        except Exception as e:
            return False, f"Backup failed: {e}"

    # 3. 写入
    try:
        new_content = content.replace(search, replace, 1)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(new_content)
    except Exception as e:
        # 回滚
        if backup and os.path.exists(bak):
            try:
                shutil.copy2(bak, file_path)
                os.remove(bak)
            except Exception:
                pass
        return False, f"Write failed (rolled back): {e}"

    # 4. 删除备份（成功）
    if backup and os.path.exists(bak):
        try:
            os.remove(bak)
        except Exception:
            pass

    return True, "✅ Edit applied successfully"


# ──────────────────────────── 批量编辑 ────────────────────────

_EDIT_BACKUP_DIR = None


def _get_backup_dir() -> str:
    """获取备份目录（延迟创建）。"""
    global _EDIT_BACKUP_DIR
    if _EDIT_BACKUP_DIR is None:
        _EDIT_BACKUP_DIR = tempfile.mkdtemp(prefix="onyx_edit_")
    return _EDIT_BACKUP_DIR


class BatchEditError(Exception):
    """批量编辑失败异常。"""
    pass


def batch_edit(edits: list) -> Tuple[bool, list]:
    """
    批量执行 SEARCH/REPLACE 编辑。

    edits: [{"path": str, "search": str, "replace": str}, ...]

    流程:
      1. 全部校验 → 任一失败则全部不执行
      2. 全部备份
      3. 逐条执行 → 任一失败则全部回滚

    返回: (all_ok: bool, results: [{"path", "ok", "message"}, ...])
    """
    results = []
    backup_dir = _get_backup_dir()

    # Step 1: 全部校验
    for edit in edits:
        ok, msg = validate_edit(edit["path"], edit["search"], edit["replace"])
        results.append({"path": edit["path"], "ok": ok, "message": msg, "edit": edit})
        if not ok:
            # 任一失败 → 全部不执行
            for r in results:
                if r["ok"]:
                    r["ok"] = False
                    r["message"] = "Cancelled (pre-check failed)"
            return False, results

    # Step 2: 全部备份
    backups = {}
    for edit in edits:
        try:
            bak = os.path.join(backup_dir, os.path.relpath(edit["path"]).replace("/", "_").replace("\\", "_"))
            shutil.copy2(edit["path"], bak)
            backups[edit["path"]] = bak
        except Exception as e:
            # 清理已创建的备份
            for b in backups.values():
                try:
                    os.remove(b)
                except Exception:
                    pass
            for r in results:
                r["ok"] = False
                r["message"] = f"Backup failed: {e}"
            return False, results

    # Step 3: 逐条执行
    try:
        for i, edit in enumerate(edits):
            ok, msg = apply_edit(edit["path"], edit["search"], edit["replace"], backup=False)
            results[i]["ok"] = ok
            results[i]["message"] = msg
            if not ok:
                raise BatchEditError(msg)
    except BatchEditError:
        # 回滚所有已修改的文件
        for edit in edits:
            if edit["path"] in backups:
                try:
                    shutil.copy2(backups[edit["path"]], edit["path"])
                except Exception:
                    pass
        # 清理备份
        for b in backups.values():
            try:
                os.remove(b)
            except Exception:
                pass
        return False, results

    # 清理备份（全部成功）
    for b in backups.values():
        try:
            os.remove(b)
        except Exception:
            pass
    return True, results


# ════════════════════════════════════════════════════════════════
# 新增：行号级操作（用于 native_fs 引擎）
# ════════════════════════════════════════════════════════════════


def read_file_lines(file_path: str) -> Tuple[bool, str, list]:
    """
    读取文件内容为行列表。

    返回: (ok: bool, error: str, lines: list)
      lines 保留原始换行符（最后一行可能无 \\n）
    """
    if not os.path.exists(file_path):
        return False, f"File not found: {file_path}", []
    if not os.path.isfile(file_path):
        return False, f"Not a file: {file_path}", []
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return True, "", content.split("\n")
    except Exception as e:
        return False, f"Read failed: {e}", []


def view_file(file_path: str, start: int = None, end: int = None) -> Tuple[bool, str, str]:
    """
    查看文件内容（精确，不截断）。

    参数:
        start: 起始行号（1-indexed, None=第1行）
        end:   结束行号（None=最后一行）

    返回: (ok: bool, error: str, content: str)
      content 不带行号前缀（由 panels.py 的 number_lines 添加）
    """
    ok, err, lines = read_file_lines(file_path)
    if not ok:
        return False, err, ""

    total = len(lines)

    if start is None:
        start_idx = 0
    else:
        start_idx = max(0, start - 1)

    if end is None:
        end_idx = total
    else:
        end_idx = min(total, end)

    if start_idx >= total or start_idx >= end_idx:
        return False, f"Invalid range: {start}-{end}, file has {total} lines", ""

    selected = lines[start_idx:end_idx]
    return True, "", "\n".join(selected)


def delete_lines(file_path: str, start: int, end: int,
                 backup: bool = True) -> Tuple[bool, str, str]:
    """
    按行号删除内容。

    参数:
        start:  起始行号（1-indexed）
        end:    结束行号（含）
        backup: 是否创建备份

    返回: (ok: bool, message: str, deleted_content: str)
    """
    ok, err, lines = read_file_lines(file_path)
    if not ok:
        return False, err, ""

    total = len(lines)
    start_1 = max(1, start)
    end_1 = min(total, end)

    if start_1 > end_1 or start_1 > total:
        return False, f"Range out of bounds: {start}-{end}, file has {total} lines", ""

    # 提取被删内容
    deleted_lines = lines[start_1 - 1:end_1]
    deleted_content = "\n".join(deleted_lines)

    # 备份
    bak = _backup_path(file_path)
    if backup:
        try:
            shutil.copy2(file_path, bak)
        except Exception as e:
            return False, f"Backup failed: {e}", deleted_content

    # 写入
    new_lines = lines[:start_1 - 1] + lines[end_1:]
    try:
        text = "\n".join(new_lines)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception as e:
        if backup and os.path.exists(bak):
            try:
                shutil.copy2(bak, file_path)
                os.remove(bak)
            except Exception:
                pass
        return False, f"Write failed (rolled back): {e}", deleted_content

    # 删除备份
    if backup and os.path.exists(bak):
        try:
            os.remove(bak)
        except Exception:
            pass

    deleted_count = end_1 - start_1 + 1
    return True, f"Deleted {deleted_count} lines ({start_1}-{end_1})", deleted_content


def insert_at_line(file_path: str, line_no: int, content: str,
                   backup: bool = True) -> Tuple[bool, str]:
    """
    在指定行后插入内容。

    参数:
        line_no: 在哪一行后插入（0=文件开头, 1=第1行后）
        content: 要插入的文本
        backup:  是否创建备份

    返回: (ok: bool, message: str)
    """
    ok, err, lines = read_file_lines(file_path)
    if not ok:
        return False, err

    total = len(lines)
    if line_no < 0 or line_no > total:
        return False, f"Line out of bounds: {line_no}, file has {total} lines"

    # 备份
    bak = _backup_path(file_path)
    if backup:
        try:
            shutil.copy2(file_path, bak)
        except Exception as e:
            return False, f"Backup failed: {e}"

    # 插入
    insert_lines = content.split("\n")
    new_lines = lines[:line_no] + insert_lines + lines[line_no:]
    try:
        text = "\n".join(new_lines)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception as e:
        if backup and os.path.exists(bak):
            try:
                shutil.copy2(bak, file_path)
                os.remove(bak)
            except Exception:
                pass
        return False, f"Write failed (rolled back): {e}"

    if backup and os.path.exists(bak):
        try:
            os.remove(bak)
        except Exception:
            pass

    return True, f"Inserted {len(insert_lines)} lines after line {line_no}"


def append_to_file(file_path: str, content: str,
                   backup: bool = True) -> Tuple[bool, str]:
    """
    追加内容到文件末尾。

    返回: (ok: bool, message: str)
    """
    if not os.path.exists(file_path):
        return False, f"File not found: {file_path}"

    # 备份
    bak = _backup_path(file_path)
    if backup:
        try:
            shutil.copy2(file_path, bak)
        except Exception as e:
            return False, f"Backup failed: {e}"

    try:
        # 检查是否需要先加换行
        with open(file_path, "r", encoding="utf-8") as f:
            existing = f.read()
        needs_newline = existing and not existing.endswith("\n")

        with open(file_path, "a", encoding="utf-8") as f:
            if needs_newline:
                f.write("\n")
            f.write(content)
            if not content.endswith("\n"):
                f.write("\n")
    except Exception as e:
        if backup and os.path.exists(bak):
            try:
                shutil.copy2(bak, file_path)
                os.remove(bak)
            except Exception:
                pass
        return False, f"Append failed (rolled back): {e}"

    if backup and os.path.exists(bak):
        try:
            os.remove(bak)
        except Exception:
            pass

    lines_added = len(content.split("\n"))
    return True, f"Appended {lines_added} lines"


def search_in_file(file_path: str, keyword: str) -> Tuple[bool, str, list]:
    """
    在文件中搜索关键词。

    返回: (ok: bool, error: str, matches: [(line_no, line_text), ...])
    """
    ok, err, lines = read_file_lines(file_path)
    if not ok:
        return False, err, []

    matches = []
    for i, line in enumerate(lines):
        if keyword in line:
            matches.append((i + 1, line))

    return True, f"Found {len(matches)} matches", matches


def get_file_summary(file_path: str) -> Tuple[bool, str, dict]:
    """
    获取文件摘要信息（不读取全部内容）。

    返回: (ok: bool, error: str, info: dict)
      info = {"lines": int, "size": int, "exists": bool}
    """
    if not os.path.exists(file_path):
        return True, "", {"lines": 0, "size": 0, "exists": False}
    if not os.path.isfile(file_path):
        return False, f"Not a file: {file_path}", {}

    try:
        size = os.path.getsize(file_path)
        # 读取前 64KB 估算行数
        with open(file_path, "rb") as f:
            chunk = f.read(65536)
        lines_estimate = chunk.count(b"\n")
        if size > 65536:
            lines_estimate = int(lines_estimate * (size / max(len(chunk), 1)))
        # 精确行数（昂贵，只在文件不太大时做）
        if size < 10 * 1024 * 1024:  # <10MB
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                exact_lines = len(f.read().split("\n"))
        else:
            exact_lines = lines_estimate

        return True, "", {"lines": exact_lines, "size": size, "exists": True}
    except Exception as e:
        return False, f"Stat failed: {e}", {}
