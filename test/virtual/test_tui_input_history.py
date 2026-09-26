#!/usr/bin/env python3
"""离线验证 TUI 输入历史：与 REPL 共用 FileHistory + 上下翻边界/草稿 + 延迟应用。

运行: python3 test/virtual/test_tui_input_history.py

要点（与实现对齐）：
  * ↑/↓ 现在是**延迟应用**（默认 0.08s 静默后才改写输入框，见 _hist_nav/_hist_commit）：
    因此每次按键后必须 `await asyncio.sleep(...)` 让 set_timer 到点，才能断言输入框内容。
  * 相邻箭头间隔必须 > _ARROW_GAP(0.35s)，否则会被判为触摸滑动（那是另一条路径）。
  * 连按不丢步：同一方向的多次箭头会累加（_hist_pending_n），到点一次补齐。
"""
import asyncio
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

try:
    import textual  # noqa: F401
except Exception as e:  # pragma: no cover
    print(f"SKIP: textual 不可用（{e}）")
    raise SystemExit(0)

SETTLE = 0.4      # > _ARROW_DEFER(0.08) 让延迟应用生效；> _ARROW_GAP(0.35) 避免被判滑动


async def _run():
    from bin.ai_tui import _build_tui
    home = tempfile.mkdtemp()
    App = _build_tui()
    app = App(session_kwargs={"user_home_dir": home}, ctx={"lang": "chinese"})
    async with app.run_test(size=(100, 30)) as pilot:
        from textual.widgets import Input
        inp = app.query_one("#prompt", Input)

        # 先停掉 worker/drain，避免 _submit 真的去跑 AI
        app._stop.set()
        await pilot.pause()

        app._submit("第一条问题")
        app._submit("第二条问题")
        await pilot.pause()

        def _reset_nav(draft=""):
            """把翻历史状态复位到「未在翻历史」，便于各用例独立。"""
            inp.value = draft
            app._hist_idx = len(app._hist)
            app._hist_draft = ""
            app._arrow_t = 0.0
            app._arrow_ts = []
            app._arrow_gesture = False
            app._hist_cancel_pending()

        # 1) 落盘且格式与 REPL 兼容
        from prompt_toolkit.history import FileHistory
        path = os.path.join(home, ".config", "onyx", "ai", "history")
        assert os.path.exists(path), "历史文件未落盘"
        loaded = list(FileHistory(path).load_history_strings())
        assert loaded[:2] == ["第二条问题", "第一条问题"], loaded
        print(f"PASS 历史落盘且 REPL 可读：{loaded[:2]}")

        # 2) ↑ / ↓ 翻历史（含边界与草稿恢复）—— 延迟应用，需等定时器到点
        _reset_nav()
        app.action_menu_up()
        await asyncio.sleep(SETTLE)
        assert inp.value == "第二条问题", inp.value
        app.action_menu_up()
        await asyncio.sleep(SETTLE)
        assert inp.value == "第一条问题", inp.value
        app.action_menu_up()          # 到顶不越界
        await asyncio.sleep(SETTLE)
        assert inp.value == "第一条问题", inp.value
        app.action_menu_down()
        await asyncio.sleep(SETTLE)
        assert inp.value == "第二条问题", inp.value
        app.action_menu_down()        # 到底恢复草稿
        await asyncio.sleep(SETTLE)
        assert inp.value == "", repr(inp.value)
        print("PASS ↑/↓ 翻历史（边界不越界、到底恢复草稿）")

        # 3) 草稿保留：先打字再上翻再下翻应还原
        _reset_nav("我的草稿")
        app.action_menu_up()
        await asyncio.sleep(SETTLE)
        assert inp.value == "第二条问题", inp.value
        app.action_menu_down()
        await asyncio.sleep(SETTLE)
        assert inp.value == "我的草稿", inp.value
        print("PASS 草稿在翻历史后原样恢复")

        # 4) 连按不丢步：三下 ↑ 间隔 < _ARROW_DEFER → 期间输入框**不得**改写；静默后一次补齐
        _reset_nav()
        for _ in range(3):
            app.action_menu_up()
            await asyncio.sleep(0.02)
            assert inp.value == "", f"延迟窗口内输入框被改写：{inp.value!r}"
        await asyncio.sleep(SETTLE)
        assert inp.value == "第一条问题", inp.value
        print("PASS 连按 ↑↑↑ 不丢步（延迟窗口内不改写输入框）")

        # 5) 滑动流（快速箭头 ≥4 次且间隔 < _ARROW_GAP）→ 滚日志，绝不改历史/输入框
        _reset_nav()
        for _ in range(5):
            app.action_menu_up()
            await asyncio.sleep(0.03)
            assert inp.value == "", f"滑动流改写了输入框：{inp.value!r}"
        await asyncio.sleep(SETTLE)
        assert inp.value == "", f"滑动流结束后输入框仍被改写：{inp.value!r}"
        assert app._hist_idx == len(app._hist), app._hist_idx
        print("PASS 滑动流不翻历史、不改写输入框（无频闪）")


def main():
    asyncio.run(_run())
    print("\nALL PASS")


if __name__ == "__main__":
    main()
