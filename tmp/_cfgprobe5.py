# -*- coding: utf-8 -*-
import asyncio, json, os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import bin.repl_config as rc

_TMP = tempfile.mkdtemp(prefix="onyx_p5_")
rc.CONFIG_DIR = os.path.join(_TMP, ".config", "onyx")
rc._FILE_SETTINGS = {k: os.path.join(rc.CONFIG_DIR, os.path.basename(v)) for k, v in rc._FILE_SETTINGS.items()}
rc.CONFIG_JSON_PATH = os.path.join(_TMP, "config.json")
rc.SANDBOX_CONFIG_PATH = os.path.join(_TMP, "sandbox")
json.dump({"display_info": {"command_prompts": {"onyx": "x"}},
           "system_info": {"current_prompt_type": "onyx", "max_history_len": 10000}},
          open(rc.CONFIG_JSON_PATH, "w", encoding="utf-8"), ensure_ascii=False)


async def main():
    cls = rc.build_tui_app("chinese")
    app = cls()
    orig = cls.on_option_list_option_selected

    def spy(self, event):
        try:
            idx = getattr(event, "option_index", None)
        except Exception:
            idx = "?"
        print(">>> App.on_option_list_option_selected idx=", idx, flush=True)
        return orig(self, event)

    cls.on_option_list_option_selected = spy
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause(0.3)
        app.query_one("#cfg-items").highlighted = 1
        await pilot.press("enter")
        await pilot.pause(0.2)
        print("stack1:", [type(s).__name__ for s in app.screen_stack], flush=True)
        app.screen.query_one("#vals").highlighted = 0
        await pilot.press("enter")
        await pilot.pause(0.3)
        print("stack2:", [type(s).__name__ for s in app.screen_stack], flush=True)
        print("debug-times =", rc.get_value("debug-times"), flush=True)


asyncio.run(main())
