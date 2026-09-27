#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""无头探测：config-onyx-repl 的 TUI 配置界面能否正常构建 / 挂载 / 取消编辑。

只做只读操作（不写任何配置）：验证 Esc 能否退出编辑弹窗。
"""
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bin.repl_config import build_tui_app  # noqa: E402

cls = build_tui_app("chinese")
print("build_tui_app ->", cls)
if cls is None:
    print("FAIL: build_tui_app 返回 None")
    sys.exit(2)

ok = True


async def main():
    global ok
    app = cls()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        print("mounted, screen =", type(app.screen).__name__)

        # 1) 打开编辑弹窗
        await pilot.press("enter")
        await pilot.pause()
        s1 = type(app.screen).__name__
        print("after enter ->", s1)
        assert s1 == "_Edit", "Enter 应打开 _Edit 弹窗"

        # 2) Esc 应取消（这是修复点）
        await pilot.press("escape")
        await pilot.pause()
        s2 = type(app.screen).__name__
        print("after escape ->", s2)
        if s2 != "Screen":
            ok = False
            print("FAIL: Esc 未能退出 _Edit（仍停留在", s2, "）")

        # 3) 再进一次，确认状态没坏
        await pilot.press("down")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        print("re-open ->", type(app.screen).__name__)
        await pilot.press("escape")
        await pilot.pause()
        print("escape again ->", type(app.screen).__name__)

        # 4) 打开键位界面，再 Esc 返回
        for _ in range(20):
            await pilot.press("down")
            await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        print("keys screen ->", type(app.screen).__name__)
        await pilot.press("escape")
        await pilot.pause()
        print("keys back ->", type(app.screen).__name__)


try:
    asyncio.run(main())
except Exception as e:
    import traceback
    traceback.print_exc()
    ok = False
    print("RUN ERROR:", repr(e))

print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
