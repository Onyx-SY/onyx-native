# -*- coding: utf-8 -*-
"""config-onyx-repl TUI：主列表含分区入口；进颜色区改色后弹窗只关一层并落盘。"""
import asyncio
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import bin.repl_config as rc  # noqa: E402

_T = tempfile.mkdtemp(prefix="onyx_tui_")
rc.CONFIG_DIR = os.path.join(_T, ".config", "onyx")
rc._FILE_SETTINGS = {k: os.path.join(rc.CONFIG_DIR, os.path.basename(v))
                     for k, v in rc._FILE_SETTINGS.items()}
rc.CONFIG_JSON_PATH = os.path.join(_T, "config.json")
rc.SANDBOX_CONFIG_PATH = os.path.join(_T, "sandbox")
rc._ptk_path = lambda: os.path.join(_T, "ptk.json")
json.dump({"display_info": {"command_prompts": {"onyx": "x", "kali": "y"}},
           "system_info": {"current_prompt_type": "onyx", "max_history_len": 10000}},
          open(rc.CONFIG_JSON_PATH, "w", encoding="utf-8"), ensure_ascii=False)


async def main():
    cls = rc.build_tui_app("chinese")
    app = cls()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause(0.3)
        ol = app.query_one("#cfg-items")
        print("主列表行数:", ol.option_count, "（设置 12 + 分区 4 + 按键 1 = 17）")
        print("前几行:", [o.prompt for o in ol.options][:3])
        print("分区行:", [o.prompt for o in ol.options if "🎨" in str(o.prompt) or "🧩" in str(o.prompt)])

        # 进「🎨 颜色」区（SETTINGS 12 行之后第一个分区）
        ol.highlighted = 12
        await pilot.press("enter")
        await pilot.pause(0.3)
        print("进入分区:", type(app.screen).__name__, "| 栈:",
              [type(s).__name__ for s in app.screen_stack])
        sec = app.screen
        items = [o.prompt for o in sec.query_one("#cfg-items").options]
        print("颜色项数:", len(items), "| 首项:", items[0])

        # 改第一项（completion-menu）
        sec.query_one("#cfg-items").highlighted = 0
        await pilot.press("enter")
        await pilot.pause(0.3)
        print("编辑弹窗:", type(app.screen).__name__, "| 栈:",
              [type(s).__name__ for s in app.screen_stack])
        ed = app.screen
        inp = ed.query_one("#val")
        inp.value = "bg:#101010 #eeeeee"
        await pilot.press("enter")
        await pilot.pause(0.3)
        print("改完后 栈:", [type(s).__name__ for s in app.screen_stack],
              "| 当前屏:", type(app.screen).__name__)
        print("落盘值:", rc.ptk_get("colors.completion-menu"))
        print("json:", json.load(open(os.path.join(_T, "ptk.json")))["colors"]["completion-menu"])

        # Esc 返回主列表
        await pilot.press("escape")
        await pilot.pause(0.3)
        print("Esc 后 栈:", [type(s).__name__ for s in app.screen_stack])
    print("TUI PASS")


asyncio.run(main())
