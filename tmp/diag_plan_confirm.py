# -*- coding: utf-8 -*-
"""诊断：TUI 下 confirm_plan 到底有没有真的等用户确认。

复刻真实调用：worker 线程里调 helpers.confirm_plan(...) → 适配器弹 SelectScreen。
分别在「用户没操作」「用户选第 3 项(摒弃)」「用户按 Esc」三种情形下看返回值。
"""
import asyncio
import os
import sys
import tempfile
import threading
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

LANG = {
    "plan_prompt": "请选择操作:",
    "plan_opt_confirm": "✅ 确认计划，开始执行",
    "plan_opt_guide": "💡 提出修改意见",
    "plan_opt_discard": "🗑️ 摒弃计划，重新制定",
}


async def main():
    from bin.ai_tui import _build_tui
    from bin.ai_lib import mode as _mode
    from bin.ai_lib.helpers import confirm_plan
    ctx = {"lang": "chinese", "session_id": str(uuid.uuid4()), "memory_mode": "global",
           "cwd": os.getcwd(), "quiet": False, "show_time": True,
           "session_start": time.time(), "mode": "normal"}
    App = _build_tui()
    app = App({"user_home_dir": tempfile.mkdtemp(prefix="onyx_plan_")}, ctx)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.6)
        _mode.set_render_mode("tui")
        print("render mode tui =", _mode.is_tui_render())
        print("adapter 已注册 =", __import__("bin.ai_lib.ui", fromlist=["x"]).get_ui_adapter() is not None)

        box = {}

        def worker():
            try:
                box["r"] = confirm_plan("## 计划\n1. 改 A\n2. 改 B", LANG)
            except Exception as e:
                box["err"] = repr(e)

        t = threading.Thread(target=worker, daemon=True)
        t.start()

        # 等模态出现
        await pilot.pause(1.0)
        print("模态栈:", [type(s).__name__ for s in app.screen_stack])
        has_modal = any(type(s).__name__ == "SelectScreen" for s in app.screen_stack)
        print("SelectScreen 已弹出 =", has_modal)
        if not has_modal:
            print("→ 未弹出模态：confirm_plan 会走什么分支？等 worker 结束")
        else:
            # 用户按 Esc（原意多半是「先不确认」）
            await pilot.press("escape")
            await pilot.pause(0.4)
        t.join(timeout=5)
        print("confirm_plan 返回 =", box.get("r"), box.get("err", ""))


asyncio.run(main())
