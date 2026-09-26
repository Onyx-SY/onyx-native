# -*- coding: utf-8 -*-
"""验证：① 钩子让新建 Console 带 ANSI；② 端到端 TUI 日志真的显示多色；③ 间距守卫存在。"""
import asyncio
import io
import os
import queue
import re
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

fails = []

# ── ① 钩子：安装后新建的 Console 必须输出 ANSI ──────────────────
from bin.ai_lib.mode import set_render_mode                      # noqa: E402
from bin.ai_tui import _QueueStream, _install_console_color_hook  # noqa: E402

set_render_mode("tui")
q = queue.Queue()
out = _QueueStream(q)
sys.stdout = out
sys.stderr = out


class _FakeApp:
    _log_w = 60
    _log_h = 20


_install_console_color_hook(_FakeApp())
from rich.console import Console                                 # noqa: E402

c = Console()                       # 模拟「钩子之后才 import 的模块」里的模块级 console
print("① is_terminal =", c.is_terminal, "| color_system =", c.color_system, file=sys.__stdout__)
c.print("  [bold green]🔧 Read[/] [cyan]path=utils.py[/] [dim]128 行[/]")
lines = []
while not q.empty():
    lines.append(q.get())
has_ansi = any("\x1b" in l for l in lines)
print("① 输出含 ANSI =", has_ansi, file=sys.__stdout__)
print("① 样例 =", repr(lines[0][:80]) if lines else "(空)", file=sys.__stdout__)
if not has_ansi:
    fails.append("钩子未生效：新建 Console 无 ANSI")
if not getattr(c, "_width", None) == 60:
    fails.append(f"宽度未对齐日志区：{getattr(c, '_width', None)}")
sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__

# ── ② 端到端：把 ANSI 行喂进 app._log，从 SVG 里取颜色 ──────────
ANSI_TOOL = ("  \x1b[1;32m🔧 Read\x1b[0m \x1b[36mpath=utils.py\x1b[0m "
             "\x1b[2m128 行\x1b[0m")


async def shot_colors():
    from bin.ai_tui import _build_tui
    ctx = {"lang": "chinese", "session_id": str(uuid.uuid4()), "memory_mode": "global",
           "cwd": os.getcwd(), "quiet": False, "show_time": True,
           "session_start": time.time(), "mode": "normal"}
    App = _build_tui()
    app = App({"user_home_dir": tempfile.mkdtemp(prefix="onyx_color_")}, ctx)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.5)
        set_render_mode("tui")
        app._log(app._turn_rule())
        app._log(ANSI_TOOL)
        app._log("   \x1b[2m→ 128 行，3 个函数\x1b[0m")
        await pilot.pause(0.4)
        svg = app.export_screenshot()
        return svg


svg = asyncio.run(shot_colors())
root = ET.fromstring(svg)
NS = "{http://www.w3.org/2000/svg}"

# Textual 的 SVG 把颜色写在 <style> 的 .terminal-*-rNN 类里，需要先解析成 class→fill
class_fill = {}
for m in re.finditer(r"\.([\w-]+)\s*\{([^}]*)\}", svg):
    fm = re.search(r"fill:\s*(#[0-9a-fA-F]{3,8})", m.group(2))
    if fm:
        class_fill[m.group(1)] = fm.group(1).lower()

rows = {}
for t in root.iter(NS + "text"):
    if "title" in (t.get("class") or ""):
        continue
    try:
        y = float(t.get("y") or 0)
        txt = "".join(t.itertext())
    except Exception:
        continue
    if not any(k in txt for k in ("Read", "path", "128")):
        continue
    fill = t.get("fill") or class_fill.get((t.get("class") or "").strip(), None)
    rows.setdefault(round(y), []).append((txt, fill))
for y in sorted(rows):
    spans = rows[y]
    fills = {f for _, f in spans}
    print(f"② y={y} 颜色数={len(fills)} {sorted(fills)}")
    print("   片段:", [(s[:22], f) for s, f in spans])
    # 只对「工具调用行」要求多色（结果行本来就是单一灰字，属正常）
    if any("🔧" in s for s, _ in spans) and len(fills) < 2:
        fails.append(f"工具调用行 y={y} 只有 {len(fills)} 种颜色（应为多色）")

# ── ③ 间距守卫（静态） ─────────────────────────────────────────
src = io.open("bin/ai_cmd.py", encoding="utf-8").read()
n_before = src.count('if _tui_mode:\n                        # TUI 无面板框线')
n_after = src.count('if _tui_mode and _tc_i == len(tool_calls) - 1:')
print("③ 工具块前置空行守卫:", n_before, "| 后置空行守卫:", n_after)
if n_before != 1 or n_after != 1:
    fails.append("工具块间距守卫缺失")

print("\nFAILS:", fails if fails else "none")
sys.exit(1 if fails else 0)
