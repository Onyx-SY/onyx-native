# -*- coding: utf-8 -*-
"""step-3/4 验证：ui 适配器在 worker 线程下弹模态框并正确回传；App 退出时不再永久阻塞。

用法：python3 tmp/tui_adapter_check.py
"""
import asyncio
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bin.ai_tui import _build_tui  # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(f"{'✅' if cond else '❌'} {name}{(' | ' + str(extra)) if extra else ''}")
    if not cond:
        FAILS.append(name)
    return cond


async def main():
    App = _build_tui()
    app = App({"user_home_dir": tempfile.mkdtemp(prefix="onyx_adapter_")},
              {"lang": "chinese", "session_id": "t", "memory_mode": "global",
               "cwd": os.getcwd()})
    res = {}

    async with app.run_test(size=(100, 30)) as pilot:
        from bin.ai_lib.ui import confirm, select_option, text_input, get_ui_adapter
        check("TUI 已注册 ui 适配器", get_ui_adapter() is not None)

        # ── 1. worker 线程里 confirm → 弹模态框 → 按 y ──
        t1 = threading.Thread(target=lambda: res.__setitem__("c", confirm("测试确认？", False, "chinese")))
        t1.start()
        await pilot.pause(0.6)
        modal_up = app.screen is not app.screen_stack[0]
        check("worker 调 confirm 会弹出模态框（不再是裸 input() 阻塞）", modal_up,
              type(app.screen).__name__)
        await pilot.press("y")
        t1.join(3)
        check("worker 拿到确认结果 True", res.get("c") is True, res.get("c"))
        check("worker 线程已结束（无永久阻塞）", not t1.is_alive())

        # ── 2. worker 线程里 text_input → 输入回传 ──
        t2 = threading.Thread(target=lambda: res.__setitem__("t", text_input("模型名", "", "chinese")))
        t2.start()
        await pilot.pause(0.6)
        for ch in ("m", "1"):
            await pilot.press(ch)
        await pilot.press("enter")
        t2.join(3)
        check("text_input 回传输入内容", res.get("t") == "m1", res.get("t"))

        # ── 3. App 退出时挂起的模态不再永久阻塞，且 select_option 返回「取消」而不是默认值 ──
        t3 = threading.Thread(
            target=lambda: res.__setitem__("s", select_option("选一个", ["A", "B", "C"], default="A")))
        t3.start()
        await pilot.pause(0.6)
        app._stop.set()        # 等价于 on_unmount 里的退出信号
        app.exit()
        t3.join(4)
        check("App 退出后 worker 不再永久阻塞", not t3.is_alive())
        check("select_option 返回空串（取消），不是默认值 A", res.get("s") == "", repr(res.get("s")))

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 项失败：{FAILS}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
