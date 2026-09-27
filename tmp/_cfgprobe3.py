# -*- coding: utf-8 -*-
"""定位 dismiss 回调抛错。"""
import asyncio, json, os, sys, tempfile, traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import bin.repl_config as rc  # noqa

_TMP = tempfile.mkdtemp(prefix="onyx_cfgprobe3_")
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
        app.query_one("#cfg-items").highlighted = 1
        await pilot.press("enter")
        await pilot.pause(0.2)
        scr = app.screen
        print("callbacks:", scr._result_callbacks)
        cb = scr._result_callbacks[-1]
        try:
            cb("true")
            print("callback OK")
        except Exception:
            traceback.print_exc()
        await pilot.pause(0.2)
        print("stack:", [type(s).__name__ for s in app.screen_stack])
    print("done")


asyncio.run(main())
