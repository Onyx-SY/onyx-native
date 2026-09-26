# -*- coding: utf-8 -*-
"""验证：计划确认必须来自用户明确选择，任何「没答上来」都不得放行。"""
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
    "plan_need_choice": "请选择一个操作（Esc 不会确认计划）",
}
fails = []


async def run_case(app, pilot, label, keys, expect, wait_first=0.8):
    """在 worker 线程跑 confirm_plan，主线程按 keys 操作模态。"""
    from bin.ai_lib.helpers import confirm_plan
    box = {}

    def worker():
        try:
            box["r"] = confirm_plan("## 计划\n1. 改 A", LANG)
        except Exception as e:
            box["err"] = repr(e)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    await pilot.pause(wait_first)
    for k in keys:
        await pilot.press(k)
        await pilot.pause(0.35)
    t.join(timeout=8)
    got = box.get("r", box.get("err", "<未返回>"))
    ok = got == expect
    print(f"{'✅' if ok else '❌'} {label}: 得到 {got!r}（期望 {expect!r}）")
    if not ok:
        fails.append(f"{label}: {got!r} != {expect!r}")


async def main():
    from bin.ai_tui import _build_tui
    from bin.ai_lib import mode as _mode
    ctx = {"lang": "chinese", "session_id": str(uuid.uuid4()), "memory_mode": "global",
           "cwd": os.getcwd(), "quiet": False, "show_time": True,
           "session_start": time.time(), "mode": "normal"}
    App = _build_tui()
    app = App({"user_home_dir": tempfile.mkdtemp(prefix="onyx_planfix_")}, ctx)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.6)
        _mode.set_render_mode("tui")

        # 1) 直接回车（默认项＝确认）→ confirm
        await run_case(app, pilot, "回车选「确认计划」", ["enter"], "confirm")

        # 2) ↓↓ 回车 → discard
        await run_case(app, pilot, "选「摒弃计划」", ["down", "down", "enter"], "discard")

        # 3) ↓ 回车 → guide
        await run_case(app, pilot, "选「提出修改意见」", ["down", "enter"], "guide")

        # 4) Esc 不得确认：Esc → 重新询问 → 再选「摒弃」应得 discard
        await run_case(app, pilot, "Esc 取消后重新询问（不应是 confirm）",
                       ["escape", "down", "down", "enter"], "discard")

        # 5) 交互不可用（模态无法推送）→ cancel，绝不执行
        from bin.ai_lib.helpers import confirm_plan
        box = {}
        orig = app.call_from_thread

        def _boom(*a, **kw):
            raise RuntimeError("push failed")

        app.call_from_thread = _boom
        try:
            def worker():
                box["r"] = confirm_plan("## 计划", LANG)
            t = threading.Thread(target=worker, daemon=True)
            t.start()
            t.join(timeout=6)
        finally:
            app.call_from_thread = orig
        got = box.get("r", "<未返回>")
        ok = got == "cancel"
        print(f"{'✅' if ok else '❌'} 模态推送失败: 得到 {got!r}（期望 'cancel'）")
        if not ok:
            fails.append(f"推送失败: {got!r}")

        # 6) blocking 不受 _MODAL_TIMEOUT 影响：把超时调小后模态仍不自动关闭
        import bin.ai_tui as _t
        old_to = _t._MODAL_TIMEOUT
        _t._MODAL_TIMEOUT = 0.4
        box2 = {}

        def worker2():
            from bin.ai_lib.helpers import confirm_plan as _cp
            box2["r"] = _cp("## 计划", LANG)

        t2 = threading.Thread(target=worker2, daemon=True)
        t2.start()
        await pilot.pause(1.4)      # 远超 0.4s 的旧超时
        still_open = any(type(s).__name__ == "SelectScreen" for s in app.screen_stack)
        print(f"{'✅' if still_open else '❌'} blocking 模式超时后模态仍打开 = {still_open}")
        if not still_open:
            fails.append("blocking 模式被 _MODAL_TIMEOUT 自动关闭")
        await pilot.press("down", "down", "enter")
        t2.join(timeout=6)
        _t._MODAL_TIMEOUT = old_to
        got2 = box2.get("r", "<未返回>")
        ok2 = got2 == "discard"
        print(f"{'✅' if ok2 else '❌'} 超时后仍可选「摒弃」: {got2!r}")
        if not ok2:
            fails.append(f"超时后选择: {got2!r}")

    # 7) 静态：ai_cmd 的 cancel 分支必须在工具执行循环之前
    src = open("bin/ai_cmd.py", encoding="utf-8").read()
    i_cancel = src.index('elif plan_choice == "cancel":')
    i_toolloop = src.index("while _tc_pending:")
    ok3 = i_cancel < i_toolloop
    print(f"{'✅' if ok3 else '❌'} cancel 分支位于工具执行之前 (cancel@{i_cancel} < tools@{i_toolloop})")
    if not ok3:
        fails.append("cancel 分支位置不对")
    ok4 = "continue_asking = False" in src[i_cancel:i_cancel + 700]
    print(f"{'✅' if ok4 else '❌'} cancel 分支会结束本轮（不执行）")
    if not ok4:
        fails.append("cancel 分支未结束本轮")

    print("\nFAILS:", fails if fails else "none")
    return 1 if fails else 0


sys.exit(asyncio.run(main()))
