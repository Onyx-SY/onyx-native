# -*- coding: utf-8 -*-
"""把 TUI 多行输入框的实际屏幕内容 dump 出来（无头，compositor 全量渲染）。

用法：python3 tmp/_screen_dump.py
"""
import asyncio
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bin.ai_tui import _build_tui  # noqa: E402


def dump(app, title=""):
    strips = app.screen._compositor.render_strips()
    print(f"\n===== {title} =====")
    for i, strip in enumerate(strips):
        text = "".join(seg.text for seg in strip)
        print(f"{i:02d}|{text}|")


async def main():
    App = _build_tui()
    home = tempfile.mkdtemp(prefix="onyx_screen_")
    app = App({"user_home_dir": home},
              {"lang": "chinese", "session_id": "t", "memory_mode": "global",
               "cwd": os.getcwd()})
    app._submit = lambda text: None
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.3)
        dump(app, "初始（单行框）")

        inp = app.query_one("#prompt")
        ml = app.query_one("#prompt-ml")

        inp.focus()
        inp.value = "hello"
        await pilot.pause(0.2)
        await pilot.press("shift+enter")
        await pilot.pause(0.2)
        dump(app, "进入多行框后（应为 hello + 空行）")

        # 输入 6 行
        for ch in "L1":
            await pilot.press(ch)
        await pilot.press("enter")
        for ch in "L2":
            await pilot.press(ch)
        await pilot.press("enter")
        for ch in "L3":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.press("L", "4")
        await pilot.press("enter")
        await pilot.press("L", "5")
        await pilot.press("enter")
        await pilot.press("L", "6")
        await pilot.pause(0.4)
        print("\nml.text =", repr(ml.text))
        print("row_map =", ml._row_map())
        print("height  =", ml.styles.height, "size =", ml.size)
        dump(app, "6 行触发折叠后")

        await pilot.press("ctrl+q")


if __name__ == "__main__":
    asyncio.run(main())
