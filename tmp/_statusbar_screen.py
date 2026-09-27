# -*- coding: utf-8 -*-
"""截图级复现：TUI 状态栏在「AI 回复完成后」是否真的画在屏幕上。"""
import asyncio
import importlib.util
import os
import sys
import threading
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location("_shotmod", os.path.join(ROOT, "tmp", "shot.py"))
_shot = importlib.util.module_from_spec(_spec)
# shot.py 顶层会跑 main()? 只有 __main__ 才跑；安全。
_spec.loader.exec_module(_shot)


def show(app, tag, cols, rows):
    bar = app.query_one("#status-bar")
    print(f"\n===== {tag} =====")
    print("  bar.display =", bar.display, " region =", bar.region, " size =", bar.size)
    try:
        svg = app.export_screenshot()
        grid = _shot.grid_of(svg, cols, rows)
        print("  ---- 屏幕 ----")
        for i, line in enumerate(grid):
            if line.strip():
                print(f"  {i:>2}| {line}")
    except Exception as e:
        print("  screenshot err:", e)


async def main():
    cols, rows = 48, 22
    from bin.ai_tui import _build_tui
    from bin.ai_lib import mode as _mode
    _mode.set_render_mode("tui")
    App = _build_tui()
    ctx = {"lang": "chinese", "session_id": str(uuid.uuid4()), "memory_mode": "global",
           "cwd": os.getcwd(), "quiet": False, "show_time": True,
           "session_start": time.time(), "mode": "normal"}
    app = App({"user_home_dir": "/tmp"}, ctx)
    async with app.run_test(size=(cols, rows)) as pilot:
        await pilot.pause(0.5)
        show(app, "A) 挂载后", cols, rows)

        # 真实路径：worker 线程 → call_from_thread → _render_status
        def push():
            app.call_from_thread(app._render_status, {
                "cwd": "/data/data/com.termux/files/home/proj",
                "ctx": 17214, "cache_pct": 88.4, "cache_supported": True,
                "balance": "12.34 CNY", "balance_platform": "deepseek"})
        threading.Thread(target=push, daemon=True).start()
        await pilot.pause(1.0)
        show(app, "B) 跨线程 set_status 之后（模拟一轮结束）", cols, rows)

        # 空闲等待：什么都不做，看是否还在
        await pilot.pause(1.5)
        show(app, "C) 空闲 1.5s 后", cols, rows)

        # 再模拟：思考 → 结束（真实轮次边界）
        app._set_thinking(True)
        await pilot.pause(0.3)
        app._set_thinking(False)
        await pilot.pause(0.8)
        show(app, "D) thinking 开关一轮后", cols, rows)


if __name__ == "__main__":
    asyncio.run(main())
