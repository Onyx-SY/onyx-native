# -*- coding: utf-8 -*-
"""定位 dismiss 不弹栈的问题。"""
import asyncio, json, os, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import bin.repl_config as rc  # noqa

_TMP = tempfile.mkdtemp(prefix="onyx_cfgprobe2_")
rc.CONFIG_DIR = os.path.join(_TMP, ".config", "onyx")
rc._FILE_SETTINGS = {k: os.path.join(rc.CONFIG_DIR, os.path.basename(v))
                     for k, v in rc._FILE_SETTINGS.items()}
rc.CONFIG_JSON_PATH = os.path.join(_TMP, "config.json")
rc.SANDBOX_CONFIG_PATH = os.path.join(_TMP, "sandbox")
json.dump({"display_info": {"command_prompts": {"onyx": "x"}},
           "system_info": {"current_prompt_type": "onyx", "max_history_len": 10000}},
          open(rc.CONFIG_JSON_PATH, "w", encoding="utf-8"), ensure_ascii=False)


async def main():
    cls = rc.build_tui_app("chinese")
    app = cls()
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause(0.3)
        ol = app.query_one("#cfg-items")
        ol.highlighted = 1
        await pilot.press("enter")
        await pilot.pause(0.2)
        print("stack:", [type(s).__name__ for s in app.screen_stack])
        scr = app.screen
        print("scr is ModalScreen:", scr.__class__.__mro__[1].__name__)
        try:
            r = scr.dismiss(None)
            print("dismiss() returned:", type(r).__name__)
        except Exception as e:
            print("dismiss raised:", type(e).__name__, e)
        await pilot.pause(0.3)
        print("stack after dismiss:", [type(s).__name__ for s in app.screen_stack])
    print("done")


asyncio.run(main())
