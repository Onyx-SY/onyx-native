# -*- coding: utf-8 -*-
"""定位多行框右缘出现的 ▅▅ 字符到底是什么。"""
import asyncio
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bin.ai_tui import _build_tui  # noqa: E402


async def main():
    App = _build_tui()
    home = tempfile.mkdtemp(prefix="onyx_art_")
    app = App({"user_home_dir": home},
              {"lang": "chinese", "session_id": "t", "memory_mode": "global",
               "cwd": os.getcwd()})
    app._submit = lambda text: None
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.3)
        inp = app.query_one("#prompt")
        ml = app.query_one("#prompt-ml")
        inp.focus()
        inp.value = "hello"
        await pilot.pause(0.2)
        await pilot.press("shift+enter")
        await pilot.pause(0.2)
        ml.text = "hello\nL1\nL2\nL3\nL4\nL5\nL6"
        await pilot.pause(0.4)

        print("show_vertical_scrollbar =", ml.show_vertical_scrollbar)
        print("scrollbar_size_vertical =", ml.styles.scrollbar_size_vertical)
        print("virtual_size =", ml.virtual_size, "content_size =", ml.content_size)
        print("scroll_offset =", ml.scroll_offset)
        print("cursor_offset =", ml._cursor_offset, "_has_cursor =", ml._has_cursor)
        print("cursor_screen_offset =", ml.cursor_screen_offset)

        strips = app.screen._compositor.render_strips()
        for i, strip in enumerate(strips):
            txt = "".join(seg.text for seg in strip)
            if "▅" in txt or "▃" in txt or "▊" in txt:
                print(f"row {i}:")
                for seg in strip:
                    if seg.text.strip():
                        print("   ", repr(seg.text), "| style:", seg.style)
        await pilot.press("ctrl+q")


if __name__ == "__main__":
    asyncio.run(main())
