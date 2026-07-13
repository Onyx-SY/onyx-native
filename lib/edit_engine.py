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
