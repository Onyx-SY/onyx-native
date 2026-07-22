"""
bin/ai_lib/ui.py — Onyx AI 终端 UI 增强模块

基于 Rich + InquirerPy 的美化交互组件。
InquirerPy 未安装时自动回退到 prompt_toolkit 原始实现。

设计原则:
  - 所有函数返回与原始实现相同的类型和语义
  - 优雅降级：不因缺少依赖而崩溃
  - 双语支持：中/英文界面自动适配
"""

import os
import sys
from typing import List, Optional, Dict, Tuple

from rich.console import Console as RichConsole
from rich.panel import Panel
from rich.table import Table
from rich.markdown import Markdown
from rich.box import ROUNDED, HEAVY, DOUBLE
from rich.text import Text
from rich.rule import Rule

console = RichConsole()

# ============================================================
# InquirerPy 优雅降级
# ============================================================
_INQUIRERPY_AVAILABLE = False
_inquirer = None

try:
    from InquirerPy import inquirer as _inquirer
    from InquirerPy.base.control import Choice
    _INQUIRERPY_AVAILABLE = True
except ImportError:
    pass


def _has_tty() -> bool:
    """检测是否有可用的 TTY（InquirerPy 需要）"""
    return sys.stdin.isatty() and sys.stdout.isatty()


# ============================================================
# 选择器 — 上下键选一项
# ============================================================

def select_option(
    message: str,
    options: List[str],
    default: str = "",
    lang: str = "chinese",
) -> str:
    """
    箭头键选择菜单。
    
    参数:
      message: 提示语
      options: 选项列表（按顺序，第一项为默认）
      default: 默认选项（为空则取 options[0]）
      lang: 语言
    
    返回: 用户选择的选项字符串
    """
    if not options:
        return ""

    default = default or options[0]

    if _INQUIRERPY_AVAILABLE and _has_tty():
        try:
            choices = options  # InquirerPy select 直接接受字符串列表
            result = _inquirer.select(
                message=message,
                choices=choices,
                default=default,
                vi_mode=False,
            ).execute()
            return result
        except (KeyboardInterrupt, EOFError):
            console.print()
            return default
        except Exception:
            pass  # 回退到 prompt_toolkit

    # ── 回退: prompt_toolkit 原始实现 ──
    return _fallback_select(message, options, default)


def _fallback_select(message: str, options: List[str], default: str) -> str:
    """prompt_toolkit 回退选择器"""
    from prompt_toolkit import prompt as pt_prompt
    from prompt_toolkit.key_binding import KeyBindings

    selected = [options.index(default) if default in options else 0]
    kb = KeyBindings()

    @kb.add("up")
    def _(event):
        selected[0] = (selected[0] - 1) % len(options)

    @kb.add("down")
    def _(event):
        selected[0] = (selected[0] + 1) % len(options)

    @kb.add("enter")
    def _(event):
        event.app.exit(result=options[selected[0]])

    def toolbar():
        lines = []
        for i, opt in enumerate(options):
            prefix = "→" if i == selected[0] else " "
            lines.append(f"  {prefix} {opt}")
        return "\n".join(lines)

    console.print(message, style="bold yellow")
    try:
        choice = pt_prompt(
            "",
            key_bindings=kb,
            bottom_toolbar=toolbar,
        )
    except (KeyboardInterrupt, EOFError):
        console.print()
        return default

    return choice or default


# ============================================================
# 确认器 — Y/n
# ============================================================

def confirm_dangerous(
    title: str,
    command: str,
    reason: str,
    lang: str = "chinese",
) -> Tuple[bool, str, str]:
    """
    危险命令确认对话框。
    
    返回: (confirmed: bool, user_response: str, refuse_reason: str)
    """
    if _INQUIRERPY_AVAILABLE and _has_tty():
        try:
            # 显示警告面板
            panel = Panel(
                f"[bold yellow]命令:[/bold yellow]\n  {command}\n\n"
                f"[bold red]风险:[/bold red]\n  {reason}",
                title=title,
                border_style="red",
                box=HEAVY,
            )
            console.print(panel)

            confirmed = _inquirer.confirm(
                message="确认执行此命令？",
                default=False,
            ).execute()

            if confirmed:
                return True, "y", ""
            else:
                refuse = _inquirer.text(
                    message="拒绝原因（可选，回车跳过）:",
                ).execute()
                refuse = refuse or "用户拒绝执行"
                return False, "n", refuse
        except (KeyboardInterrupt, EOFError):
            console.print()
            return False, "interrupt", "用户中断"
        except Exception:
            pass  # 回退

    # ── 回退: console.print + prompt ──
    return _fallback_confirm_dangerous(title, command, reason, lang)


def _fallback_confirm_dangerous(
    title: str, command: str, reason: str, lang: str
) -> Tuple[bool, str, str]:
    """prompt_toolkit 回退确认器"""
    from prompt_toolkit import prompt

    console.print(Panel(
        f"命令: {command}\n风险: {reason}",
        title=title,
        border_style="red",
        box=HEAVY,
    ))

    label = "确认执行？(y/N): " if lang == "english" else "确认执行？(y/N): "
    try:
        user_input = prompt(label).lower().strip()
    except (KeyboardInterrupt, EOFError):
        console.print()
        return False, "interrupt", "用户中断"

    if user_input == "y":
        return True, "y", ""

    # 收集拒绝原因
    reason_label = "拒绝原因（可选）: " if lang == "english" else "拒绝原因（可选）: "
    try:
        refuse = prompt(reason_label).strip()
    except (KeyboardInterrupt, EOFError):
        refuse = ""
    return False, "n", refuse or "用户拒绝执行"


# ============================================================
# 文本输入
# ============================================================

def text_input(
    message: str,
    default: str = "",
    lang: str = "chinese",
) -> str:
    """
    单行文本输入。
    """
    if _INQUIRERPY_AVAILABLE and _has_tty():
        try:
            result = _inquirer.text(
                message=message,
                default=default,
            ).execute()
            return result or default
        except (KeyboardInterrupt, EOFError):
            console.print()
            return default
        except Exception:
            pass

    # ── 回退 ──
    from prompt_toolkit import prompt
    try:
        return prompt(f"{message} ", default=default).strip() or default
    except (KeyboardInterrupt, EOFError):
        console.print()
        return default


# ============================================================
# Rich 渲染组件
# ============================================================

def render_plan_panel(plan_text: str) -> Panel:
    """渲染计划内容 Panel"""
    md = Markdown(plan_text.strip()) if plan_text.strip() else Text("(空计划)")
    return Panel(
        md,
        title="📋 AI 计划",
        border_style="cyan",
        box=ROUNDED,
        padding=(1, 2),
    )


def render_analysis_panel(analysis_text: str) -> Panel:
    """渲染策略分析 Panel"""
    return Panel(
        analysis_text.strip(),
        title="🧠 AI 决策分析",
        border_style="blue",
        box=ROUNDED,
        padding=(1, 2),
    )


def render_warning_panel(title: str, body: str) -> Panel:
    """渲染警告 Panel（红色）"""
    return Panel(
        body.strip(),
        title=title,
        border_style="red",
        box=HEAVY,
        padding=(1, 2),
    )


def render_ai_panel(text: str, title: str = "🤖 AI") -> Panel:
    """渲染 AI 回答 — 加大圆点前缀 + 极简风格"""
    content = text.strip()
    if content:
        content = "● " + content
    md = Markdown(content) if content else Text("(无内容)")
    return Panel(
        md,
        title=title,
        border_style="dim",
        box=ROUNDED,
        padding=(0, 1),
    )


def render_tool_table(tool_results: List[Dict[str, str]]) -> Table:
    """渲染工具执行结果表格"""
    table = Table(
        title="🔧 工具执行",
        box=ROUNDED,
        border_style="dim",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("#", style="dim", width=3)
    table.add_column("工具", style="bold")
    table.add_column("参数", style="dim")
    table.add_column("状态")
    table.add_column("输出")

    for i, tc in enumerate(tool_results, 1):
        status = tc.get("status", "")
        status_icon = "✅" if "ok" in status else "❌"
        status_style = "green" if "ok" in status else "red"
        table.add_row(
            str(i),
            tc.get("name", "?"),
            tc.get("params", "")[:40],
            f"[{status_style}]{status_icon}[/{status_style}]",
            tc.get("output", "")[:80],
        )
    return table


def render_separator(text: str = "") -> Rule:
    """渲染分隔线"""
    return Rule(text, style="dim")


def render_spinner(text: str = "思考中..."):
    """返回 Rich spinner 状态文本"""
    from rich.spinner import Spinner
    return Spinner("dots", text=text, style="bold cyan")


# ============================================================
# 流式展示 builder
# ============================================================

class StreamingDisplay:
    """
    流式 AI 回答展示管理器。
    
    用法:
      display = StreamingDisplay()
      with Live(display.panel, ...) as live:
          display.attach(live)
          for chunk in stream:
              display.feed(chunk)
          display.finalize(parsed_txt)
    """

    def __init__(self, lang: str = "chinese"):
        self.lang = lang
        self._live = None
        self._streamed = ""
        self._spinning = True

    @property
    def panel(self):
        """初始 Panel（思考中...）"""
        from rich.spinner import Spinner
        spinner = Spinner("dots", text=" 思考中...", style="bold cyan")
        return Panel(spinner, title="🤖 AI", border_style="green", box=ROUNDED)

    def attach(self, live):
        """绑定 Rich Live 对象"""
        self._live = live

    def feed(self, text: str):
        """追加流式文本并刷新"""
        if not self._streamed and text.strip():
            text = "● " + text
        self._streamed += text
        self._spinning = False
        if self._live:
            self._live.update(Panel(
                self._streamed,
                title="🤖 AI",
                border_style="green",
                box=ROUNDED,
            ))

    def finalize(self, parsed_text: str):
        """
        用解析后的干净文本替换流式展示。
        流式 buffer 可能因 token 切分而包含格式标记，
        这里用结构化解析后的 txt 覆盖。
        """
        final = parsed_text.strip() if parsed_text else self._streamed
        if final:
            self._spinning = False
            if self._live:
                if not final.startswith("● "):
                    final = "● " + final
                self._live.update(Panel(
                    Markdown(final),
                    title="🤖 AI",
                    border_style="green",
                    box=ROUNDED,
                ))
        elif self._spinning and self._live:
            pass  # 无内容时不显示空面板
