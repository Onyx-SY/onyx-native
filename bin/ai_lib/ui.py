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
import contextlib
from typing import List, Optional, Dict, Tuple

from rich.console import Console as RichConsole
from rich.panel import Panel
from rich.table import Table
from rich.markdown import Markdown
from rich.box import ROUNDED, HEAVY, DOUBLE
from rich.text import Text
from rich.rule import Rule

console = RichConsole()


def _ui_lang() -> str:
    """获取当前语言（延迟导入避免与 config 循环依赖）。"""
    try:
        from .config import get_current_lang
        return get_current_lang()
    except Exception:
        return "chinese"


# ============================================================
# InquirerPy 优雅降级
# ============================================================
_INQUIRERPY_AVAILABLE = False
_inquirer = None
_INQUIRERPY_TRIED = False


def _ensure_inquirer():
    """延迟加载 InquirerPy（避免模块级导入拉入 prompt_toolkit ~1s）。行为与原来完全一致。"""
    global _inquirer, _INQUIRERPY_AVAILABLE, _INQUIRERPY_TRIED
    if not _INQUIRERPY_TRIED:
        _INQUIRERPY_TRIED = True
        try:
            from InquirerPy import inquirer as _inquirer
            _INQUIRERPY_AVAILABLE = True
        except ImportError:
            _INQUIRERPY_AVAILABLE = False
    return _inquirer


def _has_tty() -> bool:
    """检测是否有可用的 TTY（InquirerPy 需要）。

    2026-09 修复：改用真实 fd 判断（os.isatty(0)/os.isatty(1)），
    免疫 capture_command_output() 把 sys.stdout 换成 RealTimeOutputCatcher
    （isatty() 恒 False）导致交互确认框被判定为无 TTY 而跳过/消失。
    """
    try:
        return os.isatty(0) and os.isatty(1)
    except Exception:
        try:
            return sys.stdin.isatty() and sys.stdout.isatty()
        except Exception:
            return False


@contextlib.contextmanager
def real_terminal_io():
    """临时把 sys.stdout/stderr 恢复为真实终端（防确认框被捕获流吞掉）。

    capture_command_output() 会把 sys.stdout 换成输出收集器：此时 input() 的
    提示词与 InquirerPy 的部分输出会写进捕获缓冲区，用户看不到确认框。
    包住确认逻辑后，提示/输入一律直达真实终端。
    """
    _out, _err = sys.stdout, sys.stderr
    try:
        if sys.stdout is not sys.__stdout__:
            sys.stdout = sys.__stdout__
        if sys.stderr is not sys.__stderr__:
            sys.stderr = sys.__stderr__
        yield
    finally:
        sys.stdout, sys.stderr = _out, _err


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

    if _ensure_inquirer() is not None and _has_tty():
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
    timeout: Optional[float] = None,
    timeout_default: bool = False,
) -> Tuple[bool, str, str]:
    """
    危险命令确认对话框。

    参数:
      timeout: 等待用户输入的秒数。None=一直等待；>0 超时后按 timeout_default 返回。
      timeout_default: 超时时的默认确认结果（True=放行 / False=拒绝）。

    返回: (confirmed: bool, user_response: str, refuse_reason: str)
    """
    # ── 2026-09：确认框全程走真实终端（防 sys.stdout 被捕获流替换导致框不可见）──
    with real_terminal_io():
        # 显示警告面板（两种路径共用）
        _cmd_label = "命令" if lang == "chinese" else "Command"
        _risk_label = "风险" if lang == "chinese" else "Risk"
        panel = Panel(
            f"[bold yellow]{_cmd_label}:[/bold yellow]\n  {command}\n\n"
            f"[bold red]{_risk_label}:[/bold red]\n  {reason}",
            title=title,
            border_style="red",
            box=HEAVY,
        )
        console.print(panel)
        try:
            sys.stdout.flush()
        except Exception:
            pass

        # 超时支持：等待输入最多 timeout 秒；无输入按 timeout_default 返回
        if timeout is not None and timeout > 0:
            if not _wait_for_input(timeout):
                if timeout_default:
                    console.print("(等待超时，已自动放行)" if lang == "chinese" else "(timed out, auto-allowed)")
                else:
                    console.print("(等待超时，已默认拒绝)" if lang == "chinese" else "(timed out, auto-denied)")
                return timeout_default, "timeout", ""

        if _ensure_inquirer() is not None and _has_tty():
            try:
                confirmed = _inquirer.confirm(
                    message="确认执行此命令？" if lang == "chinese" else "Confirm executing this command?",
                    default=False,
                ).execute()

                if confirmed:
                    return True, "y", ""
                else:
                    refuse = _inquirer.text(
                        message="拒绝原因（可选，回车跳过）:" if lang == "chinese" else "Reason to refuse (optional, Enter to skip):",
                    ).execute()
                    refuse = refuse or ("用户拒绝执行" if lang == "chinese" else "User refused")
                    return False, "n", refuse
            except (KeyboardInterrupt, EOFError):
                console.print()
                return False, "interrupt", "用户中断" if lang == "chinese" else "User interrupted"
            except Exception:
                pass  # 回退

        # ── 回退: console.print + prompt ──
        return _fallback_confirm_dangerous(title, command, reason, lang)


def _wait_for_input(seconds: float) -> bool:
    """等待用户输入最多 seconds 秒。有输入返回 True，超时返回 False。

    跨平台：POSIX 用 select，Windows 用 msvcrt 轮询。
    无法检测时保守返回 True（不自动放行，等待用户）。
    """
    if seconds <= 0:
        return True
    import time as _time
    if sys.platform == "win32":
        try:
            import msvcrt
            _deadline = _time.time() + seconds
            while _time.time() < _deadline:
                if msvcrt.kbhit():
                    return True
                _time.sleep(0.05)
            return False
        except Exception:
            return True
    try:
        import select
        _r, _, _ = select.select([sys.stdin], [], [], seconds)
        return bool(_r)
    except Exception:
        return True


def _fallback_confirm_dangerous(
    title: str, command: str, reason: str, lang: str
) -> Tuple[bool, str, str]:
    """prompt_toolkit 回退确认器（异常时再退化为纯 input，保证框一定可见可答）"""
    _cmd_label = "命令" if lang == "chinese" else "Command"
    _risk_label = "风险" if lang == "chinese" else "Risk"
    console.print(Panel(
        f"{_cmd_label}: {command}\n{_risk_label}: {reason}",
        title=title,
        border_style="red",
        box=HEAVY,
    ))
    try:
        sys.stdout.flush()
    except Exception:
        pass

    label = "Confirm? (y/N): " if lang == "english" else "确认执行？(y/N): "
    try:
        from prompt_toolkit import prompt as pt_prompt
        user_input = pt_prompt(label).lower().strip()
    except (KeyboardInterrupt, EOFError):
        console.print()
        return False, "interrupt", "User interrupted" if lang == "english" else "用户中断"
    except Exception:
        # prompt_toolkit 异常（无 TTY / 终端状态异常）→ 纯 input 兜底
        try:
            user_input = input(label).strip().lower()
        except (KeyboardInterrupt, EOFError):
            console.print()
            return False, "interrupt", "User interrupted" if lang == "english" else "用户中断"

    if user_input == "y":
        return True, "y", ""

    # 收集拒绝原因
    reason_label = "Reason to refuse (optional): " if lang == "english" else "拒绝原因（可选）: "
    try:
        from prompt_toolkit import prompt as pt_prompt
        refuse = pt_prompt(reason_label).strip()
    except (KeyboardInterrupt, EOFError):
        refuse = ""
    except Exception:
        try:
            refuse = input(reason_label).strip()
        except (KeyboardInterrupt, EOFError):
            refuse = ""
    return False, "n", refuse or ("User refused" if lang == "english" else "用户拒绝执行")


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
    if _ensure_inquirer() is not None and _has_tty():
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
    _l = _ui_lang()
    md = Markdown(plan_text.strip()) if plan_text.strip() else Text("(空计划)" if _l == "chinese" else "(empty plan)")
    return Panel(
        md,
        title="📋 AI 计划" if _l == "chinese" else "📋 AI Plan",
        border_style="cyan",
        box=ROUNDED,
        padding=(1, 2),
    )


def render_analysis_panel(analysis_text: str) -> Panel:
    """渲染策略分析 Panel"""
    _l = _ui_lang()
    return Panel(
        analysis_text.strip(),
        title="🧠 AI 决策分析" if _l == "chinese" else "🧠 AI Decision Analysis",
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
    md = Markdown(content) if content else Text("(无内容)" if _ui_lang() == "chinese" else "(empty)")
    return Panel(
        md,
        title=title,
        border_style="dim",
        box=ROUNDED,
        padding=(0, 1),
    )


def render_tool_table(tool_results: List[Dict[str, str]]) -> Table:
    """渲染工具执行结果表格"""
    _l = _ui_lang()
    table = Table(
        title="🔧 工具执行" if _l == "chinese" else "🔧 Tool Execution",
        box=ROUNDED,
        border_style="dim",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("#", style="dim", width=3)
    table.add_column("工具" if _l == "chinese" else "Tool", style="bold")
    table.add_column("参数" if _l == "chinese" else "Params", style="dim", overflow="fold")
    table.add_column("状态" if _l == "chinese" else "Status")
    table.add_column("输出" if _l == "chinese" else "Output")

    for i, tc in enumerate(tool_results, 1):
        status = tc.get("status", "")
        status_icon = "✅" if "ok" in status else "❌"
        status_style = "green" if "ok" in status else "red"
        table.add_row(
            str(i),
            tc.get("name", "?"),
            tc.get("params", ""),
            f"[{status_style}]{status_icon}[/{status_style}]",
            tc.get("output", "")[:80],
        )
    return table


def render_separator(text: str = "") -> Rule:
    """渲染分隔线"""
    return Rule(text, style="dim")


def render_spinner(text: str = ""):
    """返回 Rich spinner 状态文本"""
    from rich.spinner import Spinner
    if not text:
        text = "思考中..." if _ui_lang() == "chinese" else "Thinking..."
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
        _think = "思考中..." if self.lang == "chinese" else "Thinking..."
        spinner = Spinner("dots", text=f" {_think}", style="bold cyan")
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
