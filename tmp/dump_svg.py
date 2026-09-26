# -*- coding: utf-8 -*-
"""dump：Textual SVG 截图的 style/class/fill 结构（用于解析颜色）。"""
import asyncio
import os
import re
import sys
import tempfile
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


async def main():
    from bin.ai_tui import _build_tui
    from bin.ai_lib.mode import set_render_mode
    ctx = {"lang": "chinese", "session_id": str(uuid.uuid4()), "memory_mode": "global",
           "cwd": os.getcwd(), "quiet": False, "show_time": True,
           "session_start": time.time(), "mode": "normal"}
    App = _build_tui()
    app = App({"user_home_dir": tempfile.mkdtemp(prefix="onyx_dump_")}, ctx)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.5)
        set_render_mode("tui")
        app._log("  \x1b[1;32m🔧 Read\x1b[0m \x1b[36mpath=utils.py\x1b[0m \x1b[2m128 行\x1b[0m")
        await pilot.pause(0.4)
        svg = app.export_screenshot()
    open("tmp/_dump.svg", "w", encoding="utf-8").write(svg)
    print("style 片段:")
    for m in re.finditer(r"\.[\w-]+\s*\{[^}]*\}", svg):
        if "fill" in m.group(0):
            print("  ", m.group(0)[:90])
    print("\ntext 元素（含 Read / path / 128）:")
    for m in re.finditer(r"<text[^>]*>[^<]*(?:Read|path|128)[^<]*</text>", svg):
        print("  ", m.group(0)[:170])


asyncio.run(main())
