# -*- coding: utf-8 -*-
"""包一层 pop_screen / dismiss，观察真实流程。"""
import asyncio, json, os, sys, tempfile, traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import bin.repl_config as rc  # noqa

_TMP = tempfile.mkdtemp(prefix="onyx_cfgprobe4_")
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

    _orig_pop = app.pop_screen
    def pop():
        print(">>> pop_screen called, stack before:", [type(s).__name__ for s in app.screen_stack])
        try:
            r = _orig_pop()
            print(">>> pop_screen OK, stack after:", [type(s).__name__ for s in app.screen_stack])
            return r
        except Exception as e:
            print(">>> pop_screen RAISED:", type(e).__name__, e)
            traceback.print_exc()
            raise
    app.pop_screen = pop

    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause(0.3)
        app.query_one("#cfg-items").highlighted = 1
        await pilot.press("enter")
        await pilot.pause(0.2)
        scr = app.screen
        _orig_dismiss = scr.dismiss
        def dm(result=None):
            print(">>> dismiss called with", repr(result))
            return _orig_dismiss(result)
        scr.dismiss = dm
        try:
            scr.query_one("#vals").highlighted = 0
            await pilot.press("enter")
        except Exception:
            traceback.print_exc()
        await pilot.pause(0.3)
        print("final stack:", [type(s).__name__ for s in app.screen_stack])
    print("done")


asyncio.run(main())
