# -*- coding: utf-8 -*-
"""无头驱动 config-onyx-repl 的 TUI，复现/定位 bug（全部落在临时目录）。"""
import asyncio, json, os, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import bin.repl_config as rc  # noqa

_TMP = tempfile.mkdtemp(prefix="onyx_cfgprobe_")
rc.CONFIG_DIR = os.path.join(_TMP, ".config", "onyx")
rc._FILE_SETTINGS = {k: os.path.join(rc.CONFIG_DIR, os.path.basename(v))
                     for k, v in rc._FILE_SETTINGS.items()}
rc.CONFIG_JSON_PATH = os.path.join(_TMP, "config.json")
rc.SANDBOX_CONFIG_PATH = os.path.join(_TMP, "sandbox")
json.dump({"display_info": {"command_prompts": {"onyx": "x", "kali": "y"}},
           "system_info": {"current_prompt_type": "onyx", "max_history_len": 10000}},
          open(rc.CONFIG_JSON_PATH, "w", encoding="utf-8"), ensure_ascii=False)


async def main():
    cls = rc.build_tui_app("chinese")
    app = cls()
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause(0.3)
        ol = app.query_one("#cfg-items")
        print("items:", ol.option_count)
        print("focus:", type(app.focused).__name__)

        # 1) 打开 debug-times（index 1，bool）→ 应弹 OptionList
        ol.highlighted = 1
        await pilot.press("enter")
        await pilot.pause(0.2)
        scr = app.screen
        print("after enter -> screen:", type(scr).__name__,
              "| stack:", [type(s).__name__ for s in app.screen_stack])
        try:
            vals = scr.query_one("#vals")
            print("vals options:", [o.prompt for o in vals.options])
        except Exception as e:
            print("no #vals:", e)

        # 2) 选中 true 应写盘
        try:
            scr.query_one("#vals").highlighted = 0
            await pilot.press("enter")
            await pilot.pause(0.3)
            print("debug-times =", rc.get_value("debug-times"),
                  "| stack:", [type(s).__name__ for s in app.screen_stack])
        except Exception as e:
            print("select err:", e)

        # 3) 打开 history-len（int）→ Input；输入含 q/r 的值，看是否误触发
        print("before step3: screen=", type(app.screen).__name__,
              "| screens=", [type(s).__name__ for s in app.screen_stack])
        ol = app.query_one("#cfg-items")
        idx = [s["id"] for s in rc.SETTINGS].index("history-len")
        ol.highlighted = idx
        print("set highlighted ->", ol.highlighted, "idx=", idx)
        await pilot.press("enter")
        await pilot.pause(0.2)
        print("after enter: highlighted=", ol.highlighted, "| screen=", type(app.screen).__name__)
        scr = app.screen
        print("screen:", type(scr).__name__, "| choices_of(history-len)=", rc.choices_of("history-len"))
        try:
            for c in scr.walk_children():
                print("   ", type(c).__name__, getattr(c, 'id', None),
                      getattr(c, 'options', None) and [o.prompt for o in c.options])
        except Exception as e:
            print("walk err:", e)
        try:
            inp = scr.query_one("#val")
            inp.focus()
            await pilot.press(*list("1234"))
            await pilot.pause(0.1)
            print("input value:", inp.value, "| screen still:", type(app.screen).__name__)
            # 试 'q'（应只插入文本，不应退出）
            await pilot.press("q")
            await pilot.pause(0.2)
            print("after 'q': input=", scr.query_one("#val").value if app.screen is scr else "SCREEN GONE",
                  "| screen:", type(app.screen).__name__)
        except Exception as e:
            print("input err:", e)
    print("done")


asyncio.run(main())
