# -*- coding: utf-8 -*-
"""验证选项弹窗：一次显示 8 项 + 上下「还有 N 项」计数（双语）。"""
import asyncio
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import bin.ai_tui as m  # noqa: E402


def _txt(w):
    return str(getattr(w, "_Static__content", ""))


async def case(n_opt, lang, keys, dump=False, title=""):
    App = m._build_tui()
    home = tempfile.mkdtemp(prefix="onyx_more_")
    app = App({"user_home_dir": home},
              {"lang": lang, "session_id": "t", "memory_mode": "global",
               "cwd": os.getcwd()})
    from textual.widgets import OptionList, Static

    async with app.run_test(size=(90, 30)) as pilot:
        await pilot.pause(0.2)
        app.push_screen(m._TUI_SELECT_SCREEN("请选择：", [f"opt-{i:02d}" for i in range(1, n_opt + 1)],
                                             lang=lang))
        await pilot.pause(0.4)
        ol = app.screen.query_one("#modal-list", OptionList)
        up_w = app.screen.query_one("#modal-more-up", Static)
        dn_w = app.screen.query_one("#modal-more-down", Static)
        for k in keys:
            await pilot.press(k)
        await pilot.pause(0.3)
        print(f"[{lang} n={n_opt} {title}] 列表高={ol.size.height} 可见选项≈{ol.size.height} "
              f"scroll_y={ol.scroll_offset.y} hi={ol.highlighted}")
        print(f"    up={up_w.display} {_txt(up_w)!r}")
        print(f"    dn={dn_w.display} {_txt(dn_w)!r}")
        if dump:
            strips = app.screen._compositor.render_strips()
            for i, s in enumerate(strips):
                t = "".join(seg.text for seg in s)
                if 5 <= i <= 26 and t.strip():
                    print(f"    {i:02d}|{t}|")
        return ol.size.height, _txt(up_w), _txt(dn_w), up_w.display, dn_w.display


async def main():
    await case(20, "chinese", [], dump=True, title="初始")
    await case(20, "chinese", ["down"] * 9, title="高亮第10项")
    await case(20, "chinese", ["down"] * 19, title="高亮末项")
    await case(5, "chinese", [], title="仅5项(不折叠)")
    await case(20, "english", [], title="英文文案")
    await case(20, "english", ["down"] * 19, title="英文末项")


if __name__ == "__main__":
    asyncio.run(main())
