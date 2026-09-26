# -*- coding: utf-8 -*-
"""诊断：TUI 里工具输出的颜色到底在哪一环丢掉了。

链路：ai_cmd.console.print(markup) → Console.file(_QueueStream) → 文本行 → app._log → RichLog
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from rich.console import Console, ColorSystem      # noqa: E402
from rich.text import Text                          # noqa: E402
from bin.ai_lib.mode import set_render_mode         # noqa: E402

SAMPLE = "  [bold green]🔧 Read[/] [cyan]path=utils.py[/] [dim]128 行[/]"

print("=== A) 强制终端 + TRUECOLOR 的 Console 写进非 tty 流 ===")
buf = io.StringIO()
c = Console(file=buf, width=60)
c._force_terminal = True
c._color_system = ColorSystem.TRUECOLOR
c.print(SAMPLE)
raw = buf.getvalue()
print("raw 含 ESC:", "\x1b" in raw)
print("raw:", repr(raw[:120]))

print("\n=== B) _log 的还原逻辑（Text.from_ansi + _dim_to_grey）===")
from bin.ai_tui import _build_tui
App = _build_tui()
OnyxTUI = App
t = Text.from_ansi(raw.rstrip("\n")) if "\x1b" in raw else Text(raw)
print("spans:", t.spans)

print("\n=== C) 真实链路：Console(file=_QueueStream) + _sync_rich_width 的强制 ===")
import queue
from bin.ai_tui import _QueueStream
q = queue.Queue()
stream = _QueueStream(q)
c2 = Console(file=stream, width=60)
print("c2 color_system 初始:", c2.color_system, "| is_terminal:", c2.is_terminal)
c2._force_terminal = True
if c2._color_system is None:
    c2._color_system = ColorSystem.TRUECOLOR
c2.print(SAMPLE)
lines = []
while not q.empty():
    lines.append(q.get())
print("队列行:", lines)
print("行内 ESC:", [("\x1b" in l) for l in lines])

print("\n=== D) 纯文本行（无 ANSI）会怎样 ===")
t2 = Text("  🔧 Read path=utils.py 128 行")
print("spans:", t2.spans, "→ 无颜色，整行同色")
