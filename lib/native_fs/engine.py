"""
engine.py — Onyx 自研文件操作执行引擎

实现 VIEW/EDIT/WRITE/APPEND/INSERT/DELETE 全部操作，
每个操作触发对应的彩色面板。

配合:
  - markup_parser.py — 解析标记块
  - panels.py       — 面板渲染
  - edit_engine.py  — SEARCH/REPLACE 核心（复用现有）
"""

import os
import sys
from typing import Tuple, Optional

from lib.edit_engine import apply_edit, validate_edit
from .panels import (
    PanelManager,
    make_reading_panel,
    make_edit_panel,
    make_delete_panel,
    make_write_panel,
    make_append_panel,
    make_insert_panel,
    make_error_panel,
    make_result_panel,
    number_lines,
)
# BlockResult — 标记块执行结果
class BlockResult:
    """单个标记块的执行结果"""

    def __init__(self, block: dict, success: bool, message: str,
                 content: str = None, old_content: str = None):
        self.type = block.get("type", "unknown")
        self.path = block.get("path", "")
        self.success = success
        self.message = message
        self.content = content          # VIEW 读取的内容
        self.old_content = old_content  # DELETE/EDIT 被替换的内容

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "success": self.success,
            "path": self.path,
            "message": self.message,
            "content": self.content,
            "old_content": self.old_content,
        }

    def __repr__(self):
        status = "✅" if self.success else "❌"
        return f"<BlockResult {status} {self.type} {self.path}: {self.message}>"


def _resolve_path(path: str, cwd: str = None) -> str:
    """解析路径：相对路径用 cwd + 绝对路径直接返回"""
    if cwd and not os.path.isabs(path):
        return os.path.normpath(os.path.join(cwd, path))
    return os.path.normpath(path)


def execute_block(block: dict, cwd: str = None,
                  panel_mgr: PanelManager = None) -> BlockResult:
    """
    执行一个标记块。

    参数:
        block:     从 parse_markup() 返回的块 dict
        cwd:       工作目录（解析相对路径用）
        panel_mgr: 面板管理器（不传则无面板显示）

    返回: BlockResult
    """
    block_type = block.get("type", "unknown")
    path = block.get("path", "")
    abs_path = _resolve_path(path, cwd)

    if panel_mgr is None:
        panel_mgr = PanelManager()

    # 分发
    if block_type == "view":
        return _do_view(block, abs_path, panel_mgr)
    elif block_type == "edit":
        return _do_edit(block, abs_path, panel_mgr)
    elif block_type == "write":
        return _do_write(block, abs_path, panel_mgr)
    elif block_type == "append":
        return _do_append(block, abs_path, panel_mgr)
    elif block_type == "insert":
        return _do_insert(block, abs_path, panel_mgr)
    elif block_type == "delete":
        return _do_delete(block, abs_path, panel_mgr)
    elif block_type == "delete_by_content":
        return _do_delete_by_content(block, abs_path, panel_mgr)
    else:
        return BlockResult(block, False, f"未知操作类型: {block_type}")


def _read_file_lines(abs_path: str) -> Tuple[bool, str, list]:
    """
    读取文件内容为行列表。

    返回: (ok, error_msg, lines)
    """
    if not os.path.exists(abs_path):
        return False, f"文件不存在: {abs_path}", []
    if not os.path.isfile(abs_path):
        return False, f"不是文件: {abs_path}", []

    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        lines = content.split("\n")
        return True, "", lines
    except Exception as e:
        return False, f"读取失败: {e}", []


def _do_view(block: dict, abs_path: str, pm: PanelManager) -> BlockResult:
    """执行 VIEW 操作（100% 精确，不截断）"""
    ok, err, lines = _read_file_lines(abs_path)
    if not ok:
        pm.show_static(make_error_panel(block["path"], err))
        return BlockResult(block, False, err)

    total_lines = len(lines)
    start = block.get("start")
    end = block.get("end")
    line = block.get("line")
    search = block.get("search")

    if line is not None:
        # 单行 [VIEW:path:42]
        if 1 <= line <= total_lines:
            content = number_lines(lines[line - 1], start=line)
            display_content = content
            line_range = (line, line)
        else:
            err = f"行号越界: {line}, 文件共 {total_lines} 行"
            pm.show_static(make_error_panel(block["path"], err))
            return BlockResult(block, False, err, content=str(total_lines))

    elif start is not None and end is not None:
        # 行范围 [VIEW:path:10-30]
        start_1 = max(1, start)
        end_1 = min(total_lines, end)
        if start_1 > end_1 or start_1 > total_lines:
            err = f"行范围越界: {start}-{end}, 文件共 {total_lines} 行"
            pm.show_static(make_error_panel(block["path"], err))
            return BlockResult(block, False, err)
        selected = lines[start_1 - 1:end_1]
        content = number_lines("\n".join(selected), start=start_1)
        display_content = content
        line_range = (start_1, end_1)

    elif search:
        # 搜索 [VIEW:path:search:关键词]
        matched_lines = []
        for i, line_text in enumerate(lines):
            if search in line_text:
                matched_lines.append((i + 1, line_text))
        if not matched_lines:
            msg = f"未找到含 \"{search}\" 的行"
            pm.show_static(make_error_panel(block["path"], msg))
            return BlockResult(block, False, msg)
        lines_found = []
        for lineno, text in matched_lines:
            lines_found.append(f"{lineno}  │ {text}")
        content = "\n".join(lines_found)
        display_content = content
        line_range = None

    else:
        # 完整文件 [VIEW:path]
        content = number_lines("\n".join(lines), start=1)
        display_content = content
        line_range = None

    # 构建并显示面板
    panel = make_reading_panel(
        block["path"], display_content,
        line_range=line_range,
        total_lines=total_lines,
    )

    with pm.show_panel(panel):
        pass  # 面板显示后自动管理生命周期

    return BlockResult(block, True, f"已读取 {total_lines} 行",
                       content=content)


def _do_edit(block: dict, abs_path: str, pm: PanelManager) -> BlockResult:
    """执行 EDIT（SEARCH/REPLACE）操作"""
    search = block.get("search", "")
    replace = block.get("replace", "")

    if not search:
        err = "SEARCH 内容为空"
        pm.show_static(make_error_panel(block["path"], err))
        return BlockResult(block, False, err)

    # 校验
    ok, err_msg = validate_edit(abs_path, search, replace)
    if not ok:
        pm.show_static(make_error_panel(block["path"], err_msg))
        return BlockResult(block, False, err_msg)

    # 构建面板
    panel, dim_panel = make_edit_panel(block["path"], search, replace)

    # 执行
    with pm.show_panel(panel, dim_panel):
        success, msg = apply_edit(abs_path, search, replace, backup=True)

    return BlockResult(block, success, msg,
                       old_content=search)


def _do_write(block: dict, abs_path: str, pm: PanelManager) -> BlockResult:
    """执行 WRITE（覆盖写入/创建）操作"""
    content = block.get("content", "")
    is_new = not os.path.exists(abs_path)

    # 确保目录存在
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)

    panel, dim_panel = make_write_panel(block["path"], content, is_new=is_new)

    try:
        with pm.show_panel(panel, dim_panel):
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(content)
        return BlockResult(block, True,
                           f"{'创建' if is_new else '写入'}成功: {abs_path}")
    except Exception as e:
        pm.show_static(make_error_panel(block["path"], f"写入失败: {e}"))
        return BlockResult(block, False, f"写入失败: {e}")


def _do_append(block: dict, abs_path: str, pm: PanelManager) -> BlockResult:
    """执行 APPEND（追加）操作"""
    content = block.get("content", "")
    if not content:
        return BlockResult(block, False, "追加内容为空")

    if not os.path.exists(abs_path):
        err = f"文件不存在: {abs_path}"
        pm.show_static(make_error_panel(block["path"], err))
        return BlockResult(block, False, err)

    panel, dim_panel = make_append_panel(block["path"], content)

    try:
        with pm.show_panel(panel, dim_panel):
            # 确保文件末尾有换行再追加
            with open(abs_path, "r", encoding="utf-8") as f:
                existing = f.read()
            needs_newline = existing and not existing.endswith("\n")
            with open(abs_path, "a", encoding="utf-8") as f:
                if needs_newline:
                    f.write("\n")
                f.write(content)
                if not content.endswith("\n"):
                    f.write("\n")
        return BlockResult(block, True, f"追加成功: {abs_path}")
    except Exception as e:
        pm.show_static(make_error_panel(block["path"], f"追加失败: {e}"))
        return BlockResult(block, False, f"追加失败: {e}")


def _do_insert(block: dict, abs_path: str, pm: PanelManager) -> BlockResult:
    """执行 INSERT（指定行后插入）操作"""
    line_no = block.get("line", 0)
    content = block.get("content", "")

    ok, err, lines = _read_file_lines(abs_path)
    if not ok:
        pm.show_static(make_error_panel(block["path"], err))
        return BlockResult(block, False, err)

    if line_no < 0 or line_no > len(lines):
        err = f"行号越界: {line_no}, 文件共 {len(lines)} 行"
        pm.show_static(make_error_panel(block["path"], err))
        return BlockResult(block, False, err)

    # 在第 line_no 行后插入
    new_lines = lines[:line_no] + [content] + lines[line_no:]

    panel, dim_panel = make_insert_panel(block["path"], line_no, content)

    try:
        with pm.show_panel(panel, dim_panel):
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write("\n".join(new_lines))
        return BlockResult(block, True,
                           f"已在第 {line_no} 行后插入 {len(content.split(chr(10)))} 行")
    except Exception as e:
        pm.show_static(make_error_panel(block["path"], f"插入失败: {e}"))
        return BlockResult(block, False, f"插入失败: {e}")


def _do_delete(block: dict, abs_path: str, pm: PanelManager) -> BlockResult:
    """执行 DELETE（按行号删除）操作"""
    start = block.get("start", 0)
    end = block.get("end", 0)
    show = block.get("show", False)

    ok, err, lines = _read_file_lines(abs_path)
    if not ok:
        pm.show_static(make_error_panel(block["path"], err))
        return BlockResult(block, False, err)

    # 边界校验
    start_1 = max(1, start)
    end_1 = min(len(lines), end)
    if start_1 > end_1 or start_1 > len(lines):
        err = f"行范围越界: {start}-{end}, 文件共 {len(lines)} 行"
        pm.show_static(make_error_panel(block["path"], err))
        return BlockResult(block, False, err)

    # 提取被删内容
    deleted_lines = lines[start_1 - 1:end_1]
    deleted_content = "\n".join(deleted_lines)

    # 构建面板
    panel, dim_panel = make_delete_panel(
        block["path"], deleted_content,
        line_range=(start_1, end_1),
    )

    # 执行删除
    new_lines = lines[:start_1 - 1] + lines[end_1:]
    try:
        with pm.show_panel(panel, dim_panel):
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write("\n".join(new_lines))
        deleted_count = len(deleted_lines)
        return BlockResult(block, True,
                           f"已删除 {deleted_count} 行 ({start_1}-{end_1})",
                           old_content=deleted_content)
    except Exception as e:
        pm.show_static(make_error_panel(block["path"], f"删除失败: {e}"))
        return BlockResult(block, False, f"删除失败: {e}")


def _do_delete_by_content(block: dict, abs_path: str,
                          pm: PanelManager) -> BlockResult:
    """执行 DELETE_BY_CONTENT（按内容搜索删除）操作"""
    search = block.get("search", "")
    if not search:
        return BlockResult(block, False, "搜索内容为空")

    ok, err, lines = _read_file_lines(abs_path)
    if not ok:
        pm.show_static(make_error_panel(block["path"], err))
        return BlockResult(block, False, err)

    full_content = "\n".join(lines)
    count = full_content.count(search)

    if count == 0:
        err = f"未找到匹配内容: {search}"
        pm.show_static(make_error_panel(block["path"], err))
        return BlockResult(block, False, err)

    if count > 1:
        err = f"内容不唯一（找到 {count} 处匹配）"
        pm.show_static(make_error_panel(block["path"], err))
        return BlockResult(block, False, err)

    # 构建面板
    panel, dim_panel = make_delete_panel(block["path"], search)

    try:
        with pm.show_panel(panel, dim_panel):
            new_content = full_content.replace(search, "", 1)
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(new_content)
        return BlockResult(block, True, f"已删除匹配内容: {search}",
                           old_content=search)
    except Exception as e:
        pm.show_static(make_error_panel(block["path"], f"删除失败: {e}"))
        return BlockResult(block, False, f"删除失败: {e}")
