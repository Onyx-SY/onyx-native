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
import shutil
import glob as glob_mod
from typing import Tuple, Optional

from lib.edit_engine import apply_edit, validate_edit, is_binary_path
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
                 content: str = None, old_content: str = None,
                 raw_content: str = None,
                 start_line: int = None, end_line: int = None,
                 total_lines: int = None,
                 search: str = None, replace: str = None):
        self.type = block.get("type", "unknown")
        self.path = block.get("path", "")
        self.success = success
        self.message = message
        self.content = content           # VIEW 读取的内容（带行号，用于显示）
        self.raw_content = raw_content   # VIEW 读取的原始内容（无行号，用于 AI 回传）
        self.old_content = old_content   # DELETE/EDIT 被替换的内容
        self.start_line = start_line     # 行范围起始（1-indexed）
        self.end_line = end_line         # 行范围结束（1-indexed）
        self.total_lines = total_lines   # 文件总行数
        self.search = search             # EDIT 的 SEARCH 文本
        self.replace = replace           # EDIT 的 REPLACE 文本

    def to_dict(self) -> dict:
        d = {
            "type": self.type,
            "success": self.success,
            "path": self.path,
            "message": self.message,
            "content": self.content,
            "raw_content": self.raw_content,
            "old_content": self.old_content,
        }
        if self.start_line is not None:
            d["start_line"] = self.start_line
        if self.end_line is not None:
            d["end_line"] = self.end_line
        if self.total_lines is not None:
            d["total_lines"] = self.total_lines
        if self.search is not None:
            d["search"] = self.search
        if self.replace is not None:
            d["replace"] = self.replace
        return d

    def __repr__(self):
        status = "✅" if self.success else "❌"
        return f"<BlockResult {status} {self.type} {self.path}: {self.message}>"


def _resolve_path(path: str, cwd: str = None) -> str:
    """解析路径：相对路径用 cwd + 绝对路径直接返回"""
    if cwd and not os.path.isabs(path):
        return os.path.normpath(os.path.join(cwd, path))
    return os.path.normpath(path)


def execute_block(block: dict, cwd: str = None,
                  panel_mgr: PanelManager = None,
                  sandbox_root: str = None) -> BlockResult:
    """
    执行一个标记块。

    参数:
        block:       从 parse_markup() 返回的块 dict
        cwd:         工作目录（解析相对路径用）
        panel_mgr:   面板管理器（不传则无面板显示）
        sandbox_root: 沙箱根目录，设置后写操作会校验路径不越界

    返回: BlockResult
    """
    block_type = block.get("type", "unknown")
    path = block.get("path", "")
    abs_path = _resolve_path(path, cwd)

    if panel_mgr is None:
        panel_mgr = PanelManager()

    # ── 写操作类型集合 ──
    _mutation_types = {"edit", "edit_range", "write", "append", "insert", "delete", "delete_by_content", "replace_all"}

    # ── 沙箱路径校验（写操作）──
    if sandbox_root and block_type in _mutation_types:
        try:
            real_abs = os.path.realpath(abs_path)
            real_root = os.path.realpath(sandbox_root)
            if not real_abs.startswith(real_root + os.sep) and real_abs != real_root:
                err_msg = f"⛔ 路径越界：{abs_path} 不在沙箱目录 \"{sandbox_root}\" 内"
                pm = panel_mgr or PanelManager()
                pm.show_static(make_error_panel(abs_path, err_msg))
                return BlockResult(block, False, err_msg)
        except Exception as e:
            err_msg = f"⛔ 沙箱校验异常：{e}"
            return BlockResult(block, False, err_msg)

    # ── 保护目录校验（写操作→禁止修改核心目录）──
    if sandbox_root and block_type in _mutation_types:
        try:
            real_abs = os.path.realpath(abs_path)
            real_root = os.path.realpath(sandbox_root)
            _protected = [
                os.path.join(real_root, "onyx"),
                os.path.join(real_root, "etc", "pki"),
                os.path.join(real_root, "onyxlog"),
                os.path.join(real_root, "tools", "sys_tools"),
            ]
            for _pdir in _protected:
                _pdir_real = os.path.realpath(_pdir)
                if real_abs == _pdir_real or real_abs.startswith(_pdir_real + os.sep):
                    err_msg = f"⛔ 保护目录拦截：{abs_path} 是核心保护目录，不允许修改"
                    pm = panel_mgr or PanelManager()
                    pm.show_static(make_error_panel(abs_path, err_msg))
                    return BlockResult(block, False, err_msg)
        except Exception as e:
            err_msg = f"⛔ 保护目录校验异常：{e}"
            return BlockResult(block, False, err_msg)

    # ── 二进制文件拦截（仅阻止写操作，VIEW 仍允许）──
    if block_type in _mutation_types and is_binary_path(abs_path):
        err_msg = f"❌ 拒绝编辑二进制文件: {abs_path}（使用 shell 命令处理）"
        pm = panel_mgr or PanelManager()
        pm.show_static(make_error_panel(abs_path, err_msg))
        return BlockResult(block, False, err_msg)

    # 分发
    if block_type == "batch":
        # batch 的 path 是多文件操作，不传 abs_path
        return _do_batch(block, cwd, panel_mgr, sandbox_root=sandbox_root)
    elif block_type == "view":
        return _do_view(block, abs_path, panel_mgr)
    elif block_type == "edit":
        return _do_edit(block, abs_path, panel_mgr)
    elif block_type == "edit_range":
        return _do_edit_by_range(block, abs_path, panel_mgr)
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
    elif block_type == "replace_all":
        return _do_replace_all(block, abs_path, panel_mgr)
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
        pm.show_static(make_error_panel(abs_path, err))
        return BlockResult(block, False, err)

    total_lines = len(lines)
    start = block.get("start")
    end = block.get("end")
    line = block.get("line")
    search_text = block.get("search")

    # raw_content 始终绑定为不带行号的纯文本（用于 AI 回传）
    # content 带行号前缀（用于终端面板展示）
    raw_content = None
    display_content = None
    line_range = None

    if line is not None:
        # 单行 [VIEW:path:42]
        if 1 <= line <= total_lines:
            raw_content = lines[line - 1]
            display_content = number_lines(raw_content, start=line)
            line_range = (line, line)
        else:
            err = f"行号越界: {line}, 文件共 {total_lines} 行"
            pm.show_static(make_error_panel(abs_path, err))
            return BlockResult(block, False, err, content=str(total_lines))

    elif start is not None and end is not None:
        # 行范围 [VIEW:path:10-30]
        start_1 = max(1, start)
        end_1 = min(total_lines, end)
        if start_1 > end_1 or start_1 > total_lines:
            err = f"行范围越界: {start}-{end}, 文件共 {total_lines} 行"
            pm.show_static(make_error_panel(abs_path, err))
            return BlockResult(block, False, err)
        selected = lines[start_1 - 1:end_1]
        raw_content = "\n".join(selected)
        display_content = number_lines(raw_content, start=start_1)
        line_range = (start_1, end_1)

    elif search_text:
        # 搜索 [VIEW:path:search:关键词]
        matched = []
        for i, line_text in enumerate(lines):
            if search_text in line_text:
                matched.append((i + 1, line_text))
        if not matched:
            msg = f"未找到含 \"{search_text}\" 的行"
            pm.show_static(make_error_panel(abs_path, msg))
            return BlockResult(block, False, msg)
        raw_lines = []
        formatted_lines = []
        for lineno, text in matched:
            raw_lines.append(text)
            formatted_lines.append(f"{lineno}  │ {text}")
        raw_content = "\n".join(raw_lines)
        display_content = "\n".join(formatted_lines)
        line_range = None

    else:
        # 完整文件 [VIEW:path]
        raw_content = "\n".join(lines)
        display_content = number_lines(raw_content, start=1)
        line_range = None

    # 构建并显示面板（带行号版本）
    panel = make_reading_panel(
        abs_path, display_content,
        line_range=line_range,
        total_lines=total_lines,
    )

    with pm.show_panel(panel):
        pass  # 面板显示后自动管理生命周期

    # 返回时 content=带行号（面板用），raw_content=纯文本（AI 回传用）
    _sl = line_range[0] if line_range else None
    _el = line_range[1] if line_range else None
    # 搜索关键词传给 search 字段，AI 能理解读的是什么
    _search_kw = search_text if search_text else None
    return BlockResult(block, True, f"已读取 {total_lines} 行",
                       content=display_content,
                       raw_content=raw_content,
                       start_line=_sl, end_line=_el,
                       total_lines=total_lines,
                       search=_search_kw)


def _do_edit(block: dict, abs_path: str, pm: PanelManager) -> BlockResult:
    """执行 EDIT（SEARCH/REPLACE）操作"""
    search = block.get("search", "")
    replace = block.get("replace", "")

    if not search:
        err = "SEARCH 内容为空"
        pm.show_static(make_error_panel(abs_path, err))
        return BlockResult(block, False, err)

    # 校验
    ok, err_msg = validate_edit(abs_path, search, replace)
    if not ok:
        pm.show_static(make_error_panel(abs_path, err_msg))
        return BlockResult(block, False, err_msg)

    # 构建面板
    panel, dim_panel = make_edit_panel(abs_path, search, replace)

    # 执行
    with pm.show_panel(panel, dim_panel):
        success, msg = apply_edit(abs_path, search, replace, backup=True)

    return BlockResult(block, success, msg,
                       search=search, replace=replace,
                       old_content=search)


def _do_edit_by_range(block: dict, abs_path: str,
                      pm: PanelManager) -> BlockResult:
    """执行 EDIT 行号范围替换（无需 SEARCH，直接按行号替换）"""
    start = block.get("start", 0)
    end = block.get("end", 0)
    new_content = block.get("content", "")

    ok, err, lines = _read_file_lines(abs_path)
    if not ok:
        pm.show_static(make_error_panel(abs_path, err))
        return BlockResult(block, False, err)

    total = len(lines)
    start_1 = max(1, start)
    end_1 = min(total, end) if end > 0 else total

    if start_1 > end_1 or start_1 > total:
        err = f"行范围越界: {start}-{end}, 文件共 {total} 行"
        pm.show_static(make_error_panel(abs_path, err))
        return BlockResult(block, False, err)

    # 获取被替换的旧内容（用于面板展示）
    old_lines = lines[start_1 - 1:end_1]
    old_content = "\n".join(old_lines)

    # 构建新行列表
    new_lines_list = new_content.split("\n")
    result_lines = lines[:start_1 - 1] + new_lines_list + lines[end_1:]

    # 构建面板
    panel, dim_panel = make_edit_panel(
        abs_path,
        search=old_content,
        replace=new_content,
    )

    try:
        with pm.show_panel(panel, dim_panel):
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write("\n".join(result_lines))
        return BlockResult(
            block, True,
            f"已替换第 {start_1}-{end_1} 行（{len(new_lines_list)} 行 → {len(old_lines)} 行）",
            old_content=old_content,
            start_line=start_1, end_line=end_1,
            total_lines=total,
            search=old_content, replace=new_content,
        )
    except Exception as e:
        pm.show_static(make_error_panel(abs_path, f"替换失败: {e}"))
        return BlockResult(block, False, f"替换失败: {e}")


def _check_indentation(content: str, path: str) -> Optional[str]:
    """检查内容是否可能丢失了缩进（纯引导警告，不阻断写入）"""
    if not content:
        return None
    lines = content.split("\n")
    non_empty = [l for l in lines if l.strip()]
    if len(non_empty) < 3:
        return None
    # 如果是 HTML/XML/代码文件但每行都顶格（无缩进），发出警告
    _code_exts = {'.html', '.htm', '.xml', '.py', '.js', '.ts', '.jsx', '.tsx', '.css', '.scss', '.json', '.yaml', '.yml', '.java', '.cpp', '.c', '.h', '.hpp', '.go', '.rs', '.svelte', '.vue'}
    _, ext = os.path.splitext(path)
    if ext.lower() not in _code_exts:
        return None
    # 检查是否有任何缩进（空格或 tab 开头）
    indented = sum(1 for l in non_empty if l[0] in (' ', '\t'))
    if indented == 0 and len(non_empty) >= 3:
        return (
            "⚠️ 警告：写入内容无任何缩进（所有行均顶格），AI 可能丢失了格式。"
            "请在 prompt 中提示 AI 保留缩进。"
        )
    return None


def _do_write(block: dict, abs_path: str, pm: PanelManager) -> BlockResult:
    """执行 WRITE（覆盖写入/创建）操作"""
    content = block.get("content", "")
    is_new = not os.path.exists(abs_path)

    # 确保目录存在
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)

    # 缩进检查（仅警告，不阻断）
    indent_warn = _check_indentation(content, abs_path)
    if indent_warn:
        pm.console.print(f"  [bold yellow]{indent_warn}[/]")

    panel, dim_panel = make_write_panel(abs_path, content, is_new=is_new)

    try:
        with pm.show_panel(panel, dim_panel):
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(content)
        return BlockResult(block, True,
                           f"{'创建' if is_new else '写入'}成功: {abs_path}")
    except Exception as e:
        pm.show_static(make_error_panel(abs_path, f"写入失败: {e}"))
        return BlockResult(block, False, f"写入失败: {e}")


def _do_append(block: dict, abs_path: str, pm: PanelManager) -> BlockResult:
    """执行 APPEND（追加）操作"""
    content = block.get("content", "")
    if not content:
        return BlockResult(block, False, "追加内容为空")

    if not os.path.exists(abs_path):
        err = f"文件不存在: {abs_path}"
        pm.show_static(make_error_panel(abs_path, err))
        return BlockResult(block, False, err)

    panel, dim_panel = make_append_panel(abs_path, content)

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
        pm.show_static(make_error_panel(abs_path, f"追加失败: {e}"))
        return BlockResult(block, False, f"追加失败: {e}")


def _do_insert(block: dict, abs_path: str, pm: PanelManager) -> BlockResult:
    """执行 INSERT（指定行后插入）操作"""
    line_no = block.get("line", 0)
    content = block.get("content", "")

    ok, err, lines = _read_file_lines(abs_path)
    if not ok:
        pm.show_static(make_error_panel(abs_path, err))
        return BlockResult(block, False, err)

    if line_no < 0 or line_no > len(lines):
        err = f"行号越界: {line_no}, 文件共 {len(lines)} 行"
        pm.show_static(make_error_panel(abs_path, err))
        return BlockResult(block, False, err)

    # 在第 line_no 行后插入（正确 split 多行内容）
    insert_lines = content.split("\n")
    new_lines = lines[:line_no] + insert_lines + lines[line_no:]

    panel, dim_panel = make_insert_panel(abs_path, line_no, content)

    try:
        with pm.show_panel(panel, dim_panel):
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write("\n".join(new_lines))
        return BlockResult(block, True,
                           f"已在第 {line_no} 行后插入 {len(content.split(chr(10)))} 行")
    except Exception as e:
        pm.show_static(make_error_panel(abs_path, f"插入失败: {e}"))
        return BlockResult(block, False, f"插入失败: {e}")


def _do_delete(block: dict, abs_path: str, pm: PanelManager) -> BlockResult:
    """执行 DELETE（按行号删除）操作"""
    start = block.get("start", 0)
    end = block.get("end", 0)
    show = block.get("show", False)

    ok, err, lines = _read_file_lines(abs_path)
    if not ok:
        pm.show_static(make_error_panel(abs_path, err))
        return BlockResult(block, False, err)

    # 边界校验
    start_1 = max(1, start)
    end_1 = min(len(lines), end)
    if start_1 > end_1 or start_1 > len(lines):
        err = f"行范围越界: {start}-{end}, 文件共 {len(lines)} 行"
        pm.show_static(make_error_panel(abs_path, err))
        return BlockResult(block, False, err)

    # 提取被删内容
    deleted_lines = lines[start_1 - 1:end_1]
    deleted_content = "\n".join(deleted_lines)

    # 构建面板
    panel, dim_panel = make_delete_panel(
        abs_path, deleted_content,
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
                           old_content=deleted_content,
                           start_line=start_1, end_line=end_1,
                           total_lines=len(lines))
    except Exception as e:
        pm.show_static(make_error_panel(abs_path, f"删除失败: {e}"))
        return BlockResult(block, False, f"删除失败: {e}")


def _do_replace_all(block: dict, abs_path: str,
                    pm: PanelManager) -> BlockResult:
    """执行 REPLACE_ALL（全局搜索替换）操作"""
    glob_pattern = block.get("glob", "")
    search_text = block.get("search", "")
    replace_text = block.get("replace", "")

    if not search_text:
        return BlockResult(block, False, "搜索内容为空")
    if not glob_pattern:
        return BlockResult(block, False, "glob 模式为空")

    # 使用 abs_path 作为基准目录解析 glob
    base_dir = os.path.dirname(abs_path) if os.path.isabs(abs_path) else "."
    matched_files = glob_mod.glob(os.path.join(base_dir, glob_pattern), recursive=True)

    # 过滤出普通文件，排除二进制文件
    targets = []
    for f in matched_files:
        if os.path.isfile(f) and not is_binary_path(f):
            targets.append(f)

    if not targets:
        return BlockResult(block, False,
                           f"未找到匹配的文件: {glob_pattern}")

    # 逐文件搜索替换
    changed = []
    errors = []
    for fpath in targets:
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            count = content.count(search_text)
            if count == 0:
                continue
            new_content = content.replace(search_text, replace_text)
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(new_content)
            changed.append(f"{fpath} ({count} 处)")
        except Exception as e:
            errors.append(f"{fpath}: {e}")

    msg_parts = []
    if changed:
        msg_parts.append(f"已修改 {len(changed)} 个文件: {', '.join(changed)}")
    else:
        msg_parts.append("未找到匹配内容")

    if errors:
        msg_parts.append(f"失败 {len(errors)} 个: {', '.join(errors)}")

    success = len(changed) > 0 or len(errors) == 0
    return BlockResult(block, success, "；".join(msg_parts))


def _do_delete_by_content(block: dict, abs_path: str,
                          pm: PanelManager) -> BlockResult:
    """执行 DELETE_BY_CONTENT（按内容搜索删除）操作"""
    search = block.get("search", "")
    if not search:
        return BlockResult(block, False, "搜索内容为空")

    ok, err, lines = _read_file_lines(abs_path)
    if not ok:
        pm.show_static(make_error_panel(abs_path, err))
        return BlockResult(block, False, err)

    full_content = "\n".join(lines)
    count = full_content.count(search)

    if count == 0:
        err = f"未找到匹配内容: {search}"
        pm.show_static(make_error_panel(abs_path, err))
        return BlockResult(block, False, err)

    if count > 1:
        err = f"内容不唯一（找到 {count} 处匹配）"
        pm.show_static(make_error_panel(abs_path, err))
        return BlockResult(block, False, err)

    # 构建面板
    panel, dim_panel = make_delete_panel(abs_path, search)

    try:
        with pm.show_panel(panel, dim_panel):
            new_content = full_content.replace(search, "", 1)
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(new_content)
        return BlockResult(block, True, f"已删除匹配内容: {search}",
                           old_content=search)
    except Exception as e:
        pm.show_static(make_error_panel(abs_path, f"删除失败: {e}"))
        return BlockResult(block, False, f"删除失败: {e}")


def _do_batch(block: dict, cwd: str = None,
              pm: PanelManager = None,
              sandbox_root: str = None) -> BlockResult:
    """
    执行 BATCH（原子批量操作）块。

    先备份所有涉及的文件，然后逐个执行子块。
    任一子块失败 → 全部回滚。
    全部成功 → 清理备份。
    """
    sub_blocks = block.get("blocks", [])
    if pm is None:
        pm = PanelManager()

    if not sub_blocks:
        return BlockResult(block, False, "BATCH 块内没有操作")

    # Step 1: 收集所有要修改的文件路径
    affected_files = {}
    for sb in sub_blocks:
        sb_path = sb.get("path", "")
        if sb_path:
            abs_sb_path = _resolve_path(sb_path, cwd)
            if os.path.isfile(abs_sb_path):
                affected_files[abs_sb_path] = None

    # Step 2: 备份所有文件
    backups = {}
    try:
        for fpath in affected_files:
            bak = fpath + ".bak.batch"
            shutil.copy2(fpath, bak)
            backups[fpath] = bak
    except Exception as e:
        for b in backups.values():
            try:
                os.remove(b)
            except Exception:
                pass
        return BlockResult(block, False, f"BATCH 备份失败: {e}")

    # Step 3: 逐个执行子块
    sub_results = []
    try:
        for sb in sub_blocks:
            r = execute_block(sb, cwd, pm, sandbox_root=sandbox_root)
            sub_results.append(r)
            if not r.success:
                raise Exception(f"子操作失败: {r.message}")
    except Exception as e:
        # 回滚所有已修改的文件
        for fpath, bak in backups.items():
            try:
                if os.path.exists(bak):
                    shutil.copy2(bak, fpath)
                    os.remove(bak)
            except Exception:
                pass
        return BlockResult(block, False,
                           f"BATCH 已回滚: {e}",
                           content=str(sub_results))

    # Step 4: 全部成功，清理备份
    for bak in backups.values():
        try:
            os.remove(bak)
        except Exception:
            pass

    success_count = sum(1 for r in sub_results if r.success)
    total = len(sub_results)
    return BlockResult(
        block, True,
        f"BATCH 完成: {success_count}/{total} 个操作成功",
        content=str(sub_results),
    )
