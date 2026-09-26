# -*- coding: utf-8 -*-
"""诊断 2：ai_cmd 的 console 在 TUI 运行时到底有没有被强制成终端。"""
import asyncio
import os
import sys
import tempfile
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

print("import ai_tui 前，ai_cmd 已加载:", "bin.ai_cmd" in sys.modules)
from bin.ai_tui import _build_tui          # noqa: E402

print("import ai_tui 后，ai_cmd 已加载:", "bin.ai_cmd" in sys.modules)


async def main():
    ctx = {"lang": "chinese", "session_id": str(uuid.uuid4()), "memory_mode": "global",
           "cwd": os.getcwd(), "quiet": False, "show_time": True,
           "session_start": time.time(), "mode": "normal"}
    App = _build_tui()
    app = App({"user_home_dir": tempfile.mkdtemp(prefix="onyx_diag_")}, ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.5)
        print("run_test 内，ai_cmd 已加载:", "bin.ai_cmd" in sys.modules)
        app._measure()
        app._sync_rich_width(full=True)
        print("收集到的 Console 数:", len(getattr(app, "_rich_consoles", [])))
        import bin.ai_cmd as ac
        print("ai_cmd 加载于 sync 之后:", "bin.ai_cmd" in sys.modules)
        print("ac.console._force_terminal =", ac.console._force_terminal)
        print("ac.console.color_system     =", ac.console.color_system)
        print("ac.console.is_terminal      =", ac.console.is_terminal)
        print("ac.console.file             =", type(ac.console.file).__name__)
        print("ac.console 在收集列表里      =", ac.console in (app._rich_consoles or []))
        # 再 sync 一次，看是否补上
        app._sync_rich_width(full=True)
        print("二次 sync 后 force_terminal =", ac.console._force_terminal,
              "| color =", ac.console.color_system)


asyncio.run(main())
