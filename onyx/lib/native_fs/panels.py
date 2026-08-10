"""
panels.py — Onyx 自研文件编辑系统的彩色实时反馈面板

三色规则:
  🔴 红色    — 被删除/移除的内容
  🟢 绿色    — 新增/写入的内容
  🔵 蓝色    — 未修改的上下文/读取

面板生命周期（三段式）:
  ① 操作中 → 彩色显示（边框亮色 + 正常内容）
  ② 操作完成 → 立刻变灰（dim 边框 + 一行结果摘要）
  ③ 短暂停留后（1.5s）→ 自动消失（终端收起）

下一个操作弹出时自动覆盖前一个面板。
"""

import time
import threading
from contextlib import contextmanager
from typing import Optional, List, Tuple

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.style import Style as RichStyle
from rich.syntax import Syntax
from rich.box import ROUNDED, HEAVY
from rich.live import Live
from rich.layout import Layout


# ── 颜色常量 ──
C_RED = "red"
C_GREEN = "green"
C_BLUE = "blue"
C_YELLOW = "yellow"
C_DIM = "bright_black"
C_WHITE = "white"

# ── 面板图标 ──
ICON_READ = "📖"
ICON_EDIT = "✏️"
ICON_WRITE = "📝"
ICON_DELETE = "🗑️"
ICON_APPEND = "➕"
ICON_INSERT = "📌"
ICON_ERROR = "❌"
ICON_OK = "✅"


class PanelManager:
    """
    面板管理器 — 简化版：直接打印摘要信息，不占屏幕。
    """

    def __init__(self, console: Console = None):
        self.console = console or Console()

    @contextmanager
    def show_panel(self, panel, dim_panel=None):
        """
        显示操作摘要 — 仅打印一行状态信息。
        """
        # 直接 yield，不管理复杂生命周期
        yield None
        # 操作完成 → 打印摘要
        if dim_panel is not None:
            if isinstance(dim_panel, Panel):
                body = dim_panel.renderable if hasattr(dim_panel, 'renderable') else ""
                self.console.print(f"  {body}")
            else:
                self.console.print(dim_panel)
        else:
            self.console.print("  ✅ 完成")

    def show_static(self, panel):
        """打印静态消息（一行摘要）"""
        if isinstance(panel, Panel):
            title = getattr(panel, "title", "") or ""
            title_str = str(title.plain if hasattr(title, 'plain') else title)
            self.console.print(f"  {'⚠️' if '失败' in str(panel) else 'ℹ️'} {title_str}")
        else:
            self.console.print(panel)


# ═══════════════════════════════════════════
# 面板构建函数
# ═══════════════════════════════════════════

def make_reading_panel(path: str, content: str,
                       line_range: Tuple[int, int] = None,
                       total_lines: int = None) -> Panel:
    """
    构建读取面板（蓝色边框 + 带行号内容）。

    参数:
        path:       文件路径
        content:    带行号的内容文本（每行 "行号 │ 内容" 格式）
        line_range: 显示的行范围 (start, end)
        total_lines: 文件总行数
    """
    # 构建标题
    if line_range:
        start, end = line_range
        range_str = f" {start}-{end} 行"
        total_str = f" (共 {total_lines} 行)" if total_lines else ""
        title = f"{ICON_READ} AI 正在读取文件  {path}{range_str}{total_str}"
    else:
        total_str = f" (共 {total_lines} 行)" if total_lines else ""
        title = f"{ICON_READ} AI 正在读取文件  {path}{total_str}"

    # 人类屏幕只显示前 100 字符，完整内容只发给 AI
    if len(content) > 100:
        preview = content[:100]
        # 找到最后一个换行，避免截断行
        last_newline = preview.rfind("\n")
        if last_newline > 50:
            preview = preview[:last_newline]
        body = Text(f"{preview}\n\n... (共 {total_lines} 行, 屏幕仅预览前 100 字符)")
    else:
        body = Text(content)

    return Panel(
        body,
        title=title,
        title_align="left",
        border_style=C_BLUE,
        box=ROUNDED,
        padding=(0, 1),
    )


def _trim_for_display(text: str, max_chars: int = 200, label: str = "") -> str:
    """截断文本用于屏幕显示，保留首尾关键信息。"""
    if len(text) <= max_chars:
        return text
    lines = text.split("\n")
    if len(lines) <= 3:
        return text[:max_chars] + "\n..."
    # 显示前 2 行 + "..." + 后 1 行
    head = "\n".join(lines[:2])
    tail = lines[-1]
    return f"{head}\n... ({len(text)} chars, {len(lines)} lines)\n{tail}"


def make_edit_panel(path: str, search: str, replace: str,
                    context_before: List[str] = None,
                    context_after: List[str] = None) -> Tuple[Panel, Panel]:
    """
    构建编辑对比面板（修改前红色/修改后绿色）。

    返回: (彩色面板, 灰色摘要面板)
    """
    # 构建修改前内容（仅显示变更行，全量内容只发给 AI）
    old_text = Text()
    search_display = _trim_for_display(search, max_chars=200)
    old_text.append("修改前:\n", style=RichStyle(color=C_RED, bold=True))
    old_text.append(search_display, style=RichStyle(color=C_RED, strike=True))

    # 构建修改后内容
    new_text = Text()
    replace_display = _trim_for_display(replace, max_chars=200)
    new_text.append("\n修改后:\n", style=RichStyle(color=C_GREEN, bold=True))
    new_text.append(replace_display, style=C_GREEN)

    body = Group(old_text, new_text)

    panel = Panel(
        body,
        title=f"{ICON_EDIT} AI 正在修改文件  {path}",
        title_align="left",
        border_style=C_GREEN,
        box=ROUNDED,
        padding=(0, 1),
    )

    dim_panel = Panel(
        Text(f"✅ 替换成功 — {path}", style=C_DIM),
        title=f"{ICON_EDIT} 修改完成  {path}",
        title_align="left",
        border_style=C_DIM,
        box=ROUNDED,
        padding=(0, 1),
    )

    return panel, dim_panel


def make_delete_panel(path: str, deleted_content: str,
                      line_range: Tuple[int, int] = None) -> Tuple[Panel, Panel]:
    """
    构建删除面板（红色边框 + 红色被删内容）。

    返回: (彩色面板, 灰色摘要面板)
    """
    total_lines = len(deleted_content.split("\n"))

    if line_range:
        range_str = f" 第 {line_range[0]}-{line_range[1]} 行"
    else:
        range_str = ""

    body = Text()
    for line in deleted_content.split("\n"):
        body.append(f"  -  {line}\n", style=RichStyle(color=C_RED, strike=False))

    panel = Panel(
        body,
        title=f"{ICON_DELETE} AI 正在删除内容  {path}{range_str}",
        title_align="left",
        border_style=C_RED,
        box=ROUNDED,
        padding=(0, 1),
    )

    dim_panel = Panel(
        Text(f"✅ 已删除 {total_lines} 行 — {path}", style=C_DIM),
        title=f"{ICON_DELETE} 删除完成  {path}",
        title_align="left",
        border_style=C_DIM,
        box=ROUNDED,
        padding=(0, 1),
    )

    return panel, dim_panel


def make_write_panel(path: str, content: str,
                     is_new: bool = True) -> Tuple[Panel, Panel]:
    """
    构建写入/覆盖面板（绿色边框）。

    返回: (彩色面板, 灰色摘要面板)
    """
    lines = content.split("\n")
    total_lines = len(lines)
    total_bytes = len(content.encode("utf-8"))
    action = "创建" if is_new else "覆盖写入"

    body = Text()
    body.append(f"文件: {path}\n", style=C_WHITE)
    body.append(f"大小: {total_lines} 行, {_format_size(total_bytes)}", style=C_WHITE)

    panel = Panel(
        body,
        title=f"{ICON_WRITE} AI 正在{action}文件",
        title_align="left",
        border_style=C_GREEN,
        box=ROUNDED,
        padding=(0, 1),
    )

    dim_panel = Panel(
        Text(f"✅ {action}成功 — {path} ({total_lines} 行, {_format_size(total_bytes)})", style=C_DIM),
        title=f"{ICON_WRITE} {action}完成",
        title_align="left",
        border_style=C_DIM,
        box=ROUNDED,
        padding=(0, 1),
    )

    return panel, dim_panel


def make_append_panel(path: str, content: str) -> Tuple[Panel, Panel]:
    """
    构建追加面板（绿色边框）。

    返回: (彩色面板, 灰色摘要面板)
    """
    lines = content.split("\n")
    total_lines = len(lines)

    body = Text()
    body.append(f"文件: {path}\n", style=C_WHITE)
    body.append(f"追加 {total_lines} 行:", style=C_GREEN)
    body.append(f"\n{content}", style=C_GREEN)

    panel = Panel(
        body,
        title=f"{ICON_APPEND} AI 正在追加内容  {path}",
        title_align="left",
        border_style=C_GREEN,
        box=ROUNDED,
        padding=(0, 1),
    )

    dim_panel = Panel(
        Text(f"✅ 追加成功 — {path} (+{total_lines} 行)", style=C_DIM),
        title=f"{ICON_APPEND} 追加完成",
        title_align="left",
        border_style=C_DIM,
        box=ROUNDED,
        padding=(0, 1),
    )

    return panel, dim_panel


def make_insert_panel(path: str, line_no: int, content: str) -> Tuple[Panel, Panel]:
    """
    构建插入面板（绿色边框）。

    返回: (彩色面板, 灰色摘要面板)
    """
    lines = content.split("\n")
    total_lines = len(lines)

    body = Text()
    body.append(f"文件: {path}  插入位置: 第 {line_no} 行之后\n", style=C_WHITE)
    body.append(content, style=C_GREEN)

    panel = Panel(
        body,
        title=f"{ICON_INSERT} AI 正在插入内容  {path}",
        title_align="left",
        border_style=C_GREEN,
        box=ROUNDED,
        padding=(0, 1),
    )

    dim_panel = Panel(
        Text(f"✅ 插入成功 — {path} (第 {line_no} 行后, +{total_lines} 行)", style=C_DIM),
        title=f"{ICON_INSERT} 插入完成",
        title_align="left",
        border_style=C_DIM,
        box=ROUNDED,
        padding=(0, 1),
    )

    return panel, dim_panel


def make_error_panel(path: str, error_msg: str) -> Panel:
    """
    构建错误面板（黄色边框 — 警告级别）。
    """
    return Panel(
        Text(f"{error_msg}", style=C_YELLOW),
        title=f"{ICON_ERROR} 操作失败  {path}",
        title_align="left",
        border_style=C_YELLOW,
        box=HEAVY,
        padding=(0, 1),
    )


def make_result_panel(success_count: int, fail_count: int) -> Panel:
    """
    构建批量操作结果汇总面板。
    """
    if fail_count == 0:
        color = C_GREEN
        icon = ICON_OK
        text = f"全部操作成功 — {success_count} 个操作已完成"
    else:
        color = C_YELLOW if fail_count < success_count else C_RED
        icon = ICON_ERROR
        text = f"{success_count} 个成功, {fail_count} 个失败"

    return Panel(
        Text(text, style=color),
        title=f"{icon} 操作汇总",
        title_align="left",
        border_style=color,
        box=ROUNDED,
        padding=(0, 1),
    )


# ═══════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════

def _format_size(bytes_: int) -> str:
    """格式化文件大小"""
    if bytes_ < 1024:
        return f"{bytes_} B"
    elif bytes_ < 1024 * 1024:
        return f"{bytes_ / 1024:.1f} KB"
    else:
        return f"{bytes_ / (1024*1024):.1f} MB"


def number_lines(text: str, start: int = 1) -> str:
    """
    给文本添加行号前缀。

    返回格式:
        1  │ #include <stdio.h>
        2  │ int main() {
    ⚠️ 行号前缀仅用于定位，不属于文件内容。构造 [EDIT] 的 SEARCH 时必须剥离。
    """
    lines = text.split("\n")
    # 计算行号宽度
    width = len(str(start + len(lines) - 1))
    result = []
    for i, line in enumerate(lines):
        lineno = start + i
        result.append(f"{lineno:>{width}}  │ {line}")
    return "\n".join(result)
