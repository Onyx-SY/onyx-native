# -*- coding: utf-8 -*-
"""计划确认弹窗（正文 + 8 选项 + 上下计数）在窄屏下的高度复核。"""
import asyncio
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import bin.ai_tui as m  # noqa: E402


def _txt(w):
    return str(getattr(w, "_Static__content", ""))


async def main():
    for h in (24, 30, 40):
        App = m._build_tui()
        app = App({"user_home_dir": tempfile.mkdtemp(prefix="onyx_plan_")},
                  {"lang": "chinese", "session_id": "t", "memory_mode": "global",
                   "cwd": os.getcwd()})
        from textual.widgets import OptionList, Static
        from textual.containers import Vertical
        body = "\n".join(f"## 步骤 {i}\n- 做点事情 {i}" for i in range(1, 25))
        opts = [f"选项 {i:02d}" for i in range(1, 21)]
        async with app.run_test(size=(80, h)) as pilot:
            await pilot.pause(0.2)
            app.push_screen(m._TUI_PLAN_SCREEN("计划已就绪，确认执行？", opts, "选项 01",
                                               body=body, lang="chinese"))
            await pilot.pause(0.6)
            modal = app.screen.query_one("#modal", Vertical)
            ol = app.screen.query_one("#modal-list", OptionList)
            body_w = app.screen.query_one("#modal-body")
            up_w = app.screen.query_one("#modal-more-up", Static)
            dn_w = app.screen.query_one("#modal-more-down", Static)
            print(f"[screen_h={h}] modal={modal.size} max=88%={int(h*0.88)} "
                  f"list={ol.size.height} body={body_w.size.height} "
                  f"up={up_w.display} dn={dn_w.display}")
            print("   up:", repr(_txt(up_w)), "| dn:", repr(_txt(dn_w)))
            assert modal.size.height <= int(h * 0.88) + 1, "弹窗超过 88% 屏高"
            assert ol.size.height == 8, f"列表应露 8 项，实际 {ol.size.height}"
            assert dn_w.display and "12" in _txt(dn_w), _txt(dn_w)
            assert body_w.size.height >= 3, "正文区被挤没了"
    print("\nOK 计划确认弹窗在 24/30/40 行屏下都不溢出，列表 8 项、正文可读")


if __name__ == "__main__":
    asyncio.run(main())
