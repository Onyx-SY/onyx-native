# -*- coding: utf-8 -*-
"""诊断 3：复刻 _run_one 的真实时序，看 ai_cmd.console 何时失去颜色。"""
import os
import queue
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bin.ai_tui import _QueueStream                       # noqa: E402

q = queue.Queue()
out = _QueueStream(q)

# ① _run_one 的第一步：接管 stdout/stderr
sys.stdout = out
sys.stderr = out

# ② 紧接着惰性 import 引擎（ai_cmd 的 module-level console 在此诞生）
from bin.ai_interactive import _call_ai_engine            # noqa: E402,F401
import bin.ai_cmd as ac                                    # noqa: E402

print("ai_cmd.console.is_terminal =", ac.console.is_terminal, file=sys.__stdout__)
print("ai_cmd.console.color_system =", ac.console.color_system, file=sys.__stdout__)
print("ai_cmd.console.file =", type(ac.console.file).__name__, file=sys.__stdout__)

ac.console.print("  [bold green]🔧 Read[/] [cyan]path=utils.py[/]")
lines = []
while not q.empty():
    lines.append(q.get())
print("首轮 ANSI 行:", lines, file=sys.__stdout__)
print("首轮含 ESC:", any("\x1b" in l for l in lines), file=sys.__stdout__)

# ③ 模拟 _sync_rich_width(full=True) 之后（gc 收集）
import gc                                                  # noqa: E402
from rich.console import Console as _C, ColorSystem as _CS  # noqa: E402
found = [o for o in gc.get_objects() if isinstance(o, _C)]
print("gc 能收集到的 Console 数:", len(found), file=sys.__stdout__)
print("其中包含 ai_cmd.console:", ac.console in found, file=sys.__stdout__)
for c in found:
    c._width = 60
    c._force_terminal = True
    if getattr(c, "_color_system", None) is None:
        c._color_system = _CS.TRUECOLOR

ac.console.print("  [bold green]🔧 Read[/] [cyan]path=utils.py[/]")
lines2 = []
while not q.empty():
    lines2.append(q.get())
print("sync 后 ANSI 行:", lines2, file=sys.__stdout__)
print("sync 后含 ESC:", any("\x1b" in l for l in lines2), file=sys.__stdout__)

sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__
