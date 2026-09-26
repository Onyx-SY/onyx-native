# -*- coding: utf-8 -*-
"""step-1/7 验证：TUI 模式下 AI 输出不再套 Rich 面板（无框线），REPL 模式保持不变。

用法：python3 tmp/tui_render_check.py
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console  # noqa: E402
from rich.panel import Panel  # noqa: E402

from bin.ai_lib import mode  # noqa: E402
from bin.ai_lib import ui as ui  # noqa: E402

FAILS = []
BOX = ("╭", "╮", "╰", "╯", "┏", "┓", "┗", "┛", "│", "┃")


def check(name, cond, extra=""):
    print(f"{'✅' if cond else '❌'} {name}{(' | ' + str(extra)) if extra else ''}")
    if not cond:
        FAILS.append(name)
    return cond


def render_to_text(renderable, width=60):
    buf = io.StringIO()
    Console(file=buf, width=width, force_terminal=False, no_color=True).print(renderable)
    return buf.getvalue()


def has_box(s: str) -> bool:
    return any(ch in s for ch in BOX)


def main():
    samples = {
        "render_ai_panel": lambda: ui.render_ai_panel("你好，这是一段回答。\n\n- 要点一\n- 要点二"),
        "render_plan_panel": lambda: ui.render_plan_panel("1. 步骤一\n2. 步骤二"),
        "render_analysis_panel": lambda: ui.render_analysis_panel("分析内容"),
        "render_warning_panel": lambda: ui.render_warning_panel("⚠️ 注意", "危险内容"),
    }

    # ── TUI 模式：无 Panel、无框线 ──
    mode.set_render_mode("tui")
    for name, fn in samples.items():
        r = fn()
        text = render_to_text(r)
        check(f"TUI：{name} 不返回 Panel", not isinstance(r, Panel), type(r).__name__)
        check(f"TUI：{name} 渲染无框线", not has_box(text), repr(text[:40]))

    # ── REPL 模式：仍是 Panel（行为不变） ──
    mode.set_render_mode("repl")
    for name, fn in samples.items():
        r = fn()
        text = render_to_text(r)
        check(f"REPL：{name} 仍是 Panel", isinstance(r, Panel), type(r).__name__)
        check(f"REPL：{name} 有框线", has_box(text))

    # ── 内容仍然保留（不是空渲染） ──
    mode.set_render_mode("tui")
    text = render_to_text(ui.render_ai_panel("关键内容ABC"))
    check("TUI：回答文本仍在", "关键内容ABC" in text, repr(text.strip()[:60]))

    # ── COLUMNS 生效：宽度按环境变量走 ──
    old = os.environ.get("COLUMNS")
    os.environ["COLUMNS"] = "42"
    try:
        c = Console()
        check("COLUMNS 能控制 Rich 宽度", c.size.width == 42, c.size.width)
    finally:
        if old is None:
            os.environ.pop("COLUMNS", None)
        else:
            os.environ["COLUMNS"] = old

    mode.set_render_mode("repl")
    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 项失败：{FAILS}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
