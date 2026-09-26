# -*- coding: utf-8 -*-
"""验证：onyx 主题已注册并生效（颜色真的换成了品牌调色板）。"""
import asyncio
import os
import sys
import tempfile
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


async def main():
    from bin.ai_tui import _build_tui
    from bin.ai_lib.ui import ONYX_PALETTE, ONYX_VARS
    ctx = {"lang": "chinese", "session_id": str(uuid.uuid4()), "memory_mode": "global",
           "cwd": os.getcwd(), "quiet": False, "show_time": True,
           "session_start": time.time(), "mode": "normal"}
    App = _build_tui()
    app = App({"user_home_dir": tempfile.mkdtemp(prefix="onyx_verify_")}, ctx)
    fails = []
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.6)
        print("theme =", app.theme)
        if app.theme != "onyx":
            fails.append("theme != onyx")
        try:
            tv = app.get_theme_variable_defaults()
            print("vars ok:", all(k in tv for k in ONYX_VARS))
        except Exception as e:
            fails.append(f"vars: {e}")
        try:
            log = app.query_one("#log")
            bc = log.styles.border_left
            print("log border-left color =", bc[1].hex if bc and bc[0] else None)
        except Exception as e:
            fails.append(f"log border: {e}")
        try:
            print("screen bg =", app.screen.styles.background.hex)
        except Exception as e:
            fails.append(f"screen bg: {e}")
        # 主题里的 onyx-* 变量是否真的可解析
        try:
            val = app.get_css_variables().get("onyx-rail")
            print("onyx-rail =", val, "(expect", ONYX_VARS["onyx-rail"] + ")")
            if str(val).lower() != ONYX_VARS["onyx-rail"].lower():
                fails.append("onyx-rail mismatch")
        except Exception as e:
            fails.append(f"css vars: {e}")
    print("FAILS:", fails if fails else "none")
    return 1 if fails else 0


sys.exit(asyncio.run(main()))
