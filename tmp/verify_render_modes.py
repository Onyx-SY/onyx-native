# -*- coding: utf-8 -*-
"""渲染对照：REPL 应逐字不变；TUI 展示新的角色字形视觉语言。"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from rich.console import Console          # noqa: E402
from bin.ai_lib import mode, ui           # noqa: E402


def render(r, w=54):
    b = io.StringIO()
    Console(file=b, width=w, force_terminal=False, no_color=True).print(r)
    return b.getvalue().rstrip("\n")


print("========== REPL（应与改动前逐字一致）==========")
mode.set_render_mode("repl")
print(render(ui.render_ai_panel("你好\n\n- 一\n- 二")))
print(render(ui.render_plan_panel("1. 步骤一")))
print(render(ui.tui_plain("正文", title="🔧 工具执行", border_style="cyan")))
print(render(ui.render_separator("sep")))

print("========== TUI（新视觉语言）==========")
mode.set_render_mode("tui")
cases = [
    ("ai", ui.render_ai_panel("你好\n\n- 一\n- 二")),
    ("plan", ui.render_plan_panel("1. 步骤一")),
    ("analysis", ui.render_analysis_panel("分析")),
    ("warn", ui.render_warning_panel("⚠️ 注意", "危险")),
    ("tool-ok", ui.tui_plain("128 行", title="✅ Read utils.py", border_style="dim green")),
    ("tool-err", ui.tui_plain("boom", title="❌ Run pytest", border_style="dim red")),
    ("sep", ui.render_separator("sep")),
]
for name, r in cases:
    print(f"--- {name} ---")
    print(render(r))
