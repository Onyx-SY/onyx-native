# -*- coding: utf-8 -*-
"""复核：TUI ConfirmScreen 里按 Enter 是否等价于点「Yes」（危险命令确认被误判为已确认）。"""
import asyncio
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import bin.ai_tui as m  # noqa: E402


async def main():
    App = m._build_tui()
    app = App({"user_home_dir": tempfile.mkdtemp(prefix="onyx_cf_")},
              {"lang": "chinese", "session_id": "t", "memory_mode": "global",
               "cwd": os.getcwd()})
    app._submit = lambda text: None

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.3)
        results = []

        # 通过 ui 适配器走真实链路
        ad = app._ui_adapter if hasattr(app, "_ui_adapter") else None
        print("has _ui_adapter:", ad is not None)

        # 直接推一个 ConfirmScreen，检查焦点与 Enter 行为
        from textual.widgets import Button
        r = {}

        def _cb(res):
            r["v"] = res

        app.push_screen(m._TUI_CONFIRM_SCREEN("危险命令：rm -rf / 确认执行？", False, "chinese"), _cb)
        await pilot.pause(0.4)
        print("focused =", type(app.focused).__name__, getattr(app.focused, "id", None))
        await pilot.press("enter")
        await pilot.pause(0.3)
        print("按 Enter 后回调结果 =", r.get("v"))
        results.append(("enter", r.get("v")))

    # 第二次：按 n 应是否决
    App2 = m._build_tui()
    app2 = App2({"user_home_dir": tempfile.mkdtemp(prefix="onyx_cf_")},
                {"lang": "chinese", "session_id": "t", "memory_mode": "global",
                 "cwd": os.getcwd()})
    app2._submit = lambda text: None
    async with app2.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.3)
        r2 = {}
        app2.push_screen(m._TUI_CONFIRM_SCREEN("危险命令：确认执行？", False, "chinese"),
                         lambda v: r2.__setitem__("v", v))
        await pilot.pause(0.4)
        await pilot.press("n")
        await pilot.pause(0.3)
        print("按 n 后回调结果 =", r2.get("v"))

    print("\n结论：Enter ->", results[0][1], "（True 表示被当成「已确认」，即危险命令未等人工确认就执行）")


if __name__ == "__main__":
    asyncio.run(main())
