#!/usr/bin/env python3
"""离线验证 TUI 稳定性（无头 Pilot）：Ctrl+C / worker 兜底 / 模态超时 / 退出清理。

运行: python3 test/virtual/test_tui_stability.py
"""
import asyncio
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

try:
    import textual  # noqa: F401
except Exception as e:  # pragma: no cover
    print(f"SKIP: textual 不可用（{e}）")
    raise SystemExit(0)


async def _run():
    from bin.ai_tui import _build_tui
    from bin.ai_lib.ui import get_ui_adapter
    App = _build_tui()
    app = App(session_kwargs={}, ctx={"lang": "chinese"})
    async with app.run_test(size=(100, 30)) as pilot:
        from textual.widgets import Input, Static
        from textual.screen import ModalScreen

        # 1) Ctrl+C 必须触发 action_cancel（修复前被聚焦 Input 的 ctrl+c=copy 抢走）
        called = {"n": 0}
        app.action_cancel = lambda: called.__setitem__("n", called["n"] + 1)
        app.query_one("#prompt", Input).focus()
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert called["n"] >= 1, "Ctrl+C 未触发 action_cancel（被 Input 抢走）"
        print("PASS Ctrl+C 触发 action_cancel")

        # 2) worker 兜底：BaseException（SystemExit 等）不得杀死线程
        def _boom(_text):
            raise SystemExit("boom")
        app._run_one = _boom
        app._in_q.put_nowait("x")
        for _ in range(20):
            await pilot.pause()
            await asyncio.sleep(0.05)
        assert app._threads[0].is_alive(), "worker 线程被 BaseException 杀死 → 队列会永久堵死"
        print("PASS worker 遇 BaseException 仍存活")

        # 3) 模态超时：回调永不到达也不能永久阻塞
        class _Never(ModalScreen):
            def compose(self):
                yield Static("never")

        box = {}

        def _call():
            box["r"] = app._adapter._modal(_Never(), timeout=0.5, on_timeout="TIMEOUT")

        threading.Thread(target=_call, daemon=True).start()
        for _ in range(30):
            await pilot.pause()
            await asyncio.sleep(0.05)
        assert box.get("r") == "TIMEOUT", box
        print("PASS 模态超时返回，不永久阻塞 worker")

    # 4) 退出清理
    assert app._stop.is_set(), "退出未设置停止标志"
    assert app._capture_stream is None, "退出未还原 stdout 捕获流"
    assert get_ui_adapter() is None, "退出未注销 UI 适配器"
    print("PASS 退出清理（停线程 / 还原 stdout / 注销适配器）")


def main():
    asyncio.run(_run())
    print("\nALL PASS")


if __name__ == "__main__":
    main()
