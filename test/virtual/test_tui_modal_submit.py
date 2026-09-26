#!/usr/bin/env python3
"""弹窗输入隔离回归：模态框里的输入绝不能变成发给 AI 的消息。

根因：Textual 的 `Input.Submitted` 会从**任意**输入框沿 DOM 冒泡到 App —— 包括模态框里的
`#modal-input`（历史搜索 / 文本输入 / 验证码）。App 的 `on_input_submitted` 不校验来源时，
「在弹窗里打完字按回车」就会把内容 `_submit()` 给 AI。

运行: python3 test/virtual/test_tui_modal_submit.py
"""
import asyncio
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

try:
    import textual  # noqa: F401
except Exception as e:  # pragma: no cover
    print(f"SKIP: textual 不可用（{e}）")
    raise SystemExit(0)


async def _run():
    from bin.ai_tui import _build_tui
    from textual.screen import ModalScreen
    from textual.widgets import Input

    App = _build_tui()
    app = App(session_kwargs={}, ctx={"lang": "chinese"})

    class _TestModal(ModalScreen):
        """模拟 HistorySearchScreen / TextScreen：弹窗内有一个输入框。"""

        def compose(self):
            yield Input(id="modal-input")

        def on_input_submitted(self, event):
            self.dismiss(event.value)

    async with app.run_test(size=(100, 30)) as pilot:
        sent = []
        app._run_one = lambda text: sent.append(text)      # 不真发请求
        app._hist_add = lambda text: None                  # 不污染历史文件

        def _wait_sent(n, timeout=2.0):
            t0 = time.time()
            while time.time() - t0 < timeout and len(sent) < n:
                time.sleep(0.02)

        # ── 1) 弹窗里输入 + 回车 → 绝不能进 AI 队列 ──
        box = {}
        app.push_screen(_TestModal(), lambda r: box.update(r=r))
        await pilot.pause()
        assert isinstance(app.screen, ModalScreen), "模态框未打开"
        await pilot.press("h", "e", "l", "l", "o")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        _wait_sent(1, timeout=0.6)
        assert box.get("r") == "hello", f"弹窗自身应收到输入，实际 {box.get('r')!r}"
        assert sent == [], f"弹窗里的输入被当成 AI 消息发出去了：{sent!r}"
        print("PASS 弹窗内输入回车 → 不会发给 AI（弹窗自身正常收到）")

        # ── 2) 主输入框回车 → 仍然正常发送 ──
        inp = app.query_one("#prompt", Input)
        inp.focus()
        await pilot.pause()
        await pilot.press("w", "o", "r", "l", "d")
        await pilot.press("enter")
        _wait_sent(1)
        assert sent == ["world"], f"主输入框应正常发送，实际 {sent!r}"
        assert inp.value == "", "发送后主输入框应清空"
        print("PASS 主输入框回车 → 正常发送并清空")

        # ── 3) 弹窗打开期间，主输入框内容变化也不会被提交 ──
        app.push_screen(_TestModal(), lambda r: None)
        await pilot.pause()
        assert app._modal_open() is True, "_modal_open() 应识别模态框"
        sent.clear()
        app._submit("should-not-be-blocked-by-modal-guard")
        _wait_sent(1, timeout=0.4)
        assert sent == ["should-not-be-blocked-by-modal-guard"], "显式 _submit 不受影响"
        print("PASS _modal_open() 正确识别模态态（仅拦截事件路径，不拦显式提交）")


def main():
    asyncio.run(_run())
    print("\nALL PASS")


if __name__ == "__main__":
    main()
