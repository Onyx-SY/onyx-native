# -*- coding: utf-8 -*-
"""调试 PromptArea 的高度/边框计算。"""
import asyncio
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import bin.ai_tui as ai_tui  # noqa: E402


async def main():
    print("ai_tui file =", ai_tui.__file__)
    App = ai_tui._build_tui()
    home = tempfile.mkdtemp(prefix="onyx_dbg_")
    app = App({"user_home_dir": home},
              {"lang": "chinese", "session_id": "t", "memory_mode": "global",
               "cwd": os.getcwd()})
    app._submit = lambda text: None
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.3)
        inp = app.query_one("#prompt")
        ml = app.query_one("#prompt-ml")
        print("class =", type(ml), type(ml).__module__)
        inp.focus()
        inp.value = "hello"
        await pilot.pause(0.2)
        await pilot.press("shift+enter")
        await pilot.pause(0.3)
        for tag in ("after-enter-multiline",):
            print(f"[{tag}] text={ml.text!r}")
            print("   styles.height =", ml.styles.height)
            print("   size          =", ml.size)
            print("   content_size  =", ml.content_size)
            print("   outer_size    =", ml.outer_size)
            print("   border_top    =", ml.styles.border_top)
            print("   border_bottom =", ml.styles.border_bottom)
            print("   padding       =", ml.styles.padding)
            print("   _chrome_height() =", ml._chrome_height())
            print("   row_map len   =", len(ml._row_map()))
        await pilot.press("ctrl+q")


if __name__ == "__main__":
    asyncio.run(main())
