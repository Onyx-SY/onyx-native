#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归：TUI 计划/选项弹窗的「键盘防误触」宽限窗口。

真实 bug：在 TUI 里 AI 提交计划后，弹窗刚出现，发送消息那次回车（或终端残留按键）
被 OptionList 当成「选中默认项」—— 默认项就是「确认计划」→ 用户还没点击，程序就把
计划当成已确认返回给 AI，AI 直接开跑。

本测试直接推入弹窗，验证：
  1. 宽限窗口内到达的回车 → 不 dismiss（不返回）；
  2. 窗口外回车 → 正常选中并 dismiss；
  3. Esc 语义不变（立即送回取消哨兵）。

运行: python3 test/virtual/test_tui_modal_no_autoselect.py
"""
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

try:
    import textual  # noqa: F401
except Exception as e:  # pragma: no cover
    print(f"SKIP: textual 不可用（{e}）")
    raise SystemExit(0)


async def _run():
    import bin.ai_tui as m
    from textual import events

    App = m._build_tui()
    app = App(session_kwargs={}, ctx={"lang": "chinese"})
    async with app.run_test(size=(100, 30)) as pilot:
        Screen = m._TUI_PLAN_SCREEN
        assert Screen is not None, "计划弹窗类未挂载"

        # ── 1) 宽限窗口内回车被忽略 ──
        got = {}
        screen = Screen("请选择操作", ["确认", "修改", "放弃"],
                        default="确认", body="计划正文", lang="chinese")
        app.push_screen(screen, lambda r: got.__setitem__("r", r))
        await pilot.pause()
        app.post_message(events.Key("enter", "\r"))
        await pilot.pause()
        assert "r" not in got, "宽限窗口内回车被当成选择（未点击即返回）"
        print("PASS 计划弹窗：宽限窗口内回车被忽略（未点击不返回）")

        # ── 2) 窗口外回车 → 正常选中默认项 ──
        await asyncio.sleep(0.4)
        app.post_message(events.Key("enter", "\r"))
        await pilot.pause()
        assert got.get("r") == "确认", f"窗口外回车应选中默认项，实际 {got.get('r')!r}"
        print("PASS 计划弹窗：宽限窗口外回车正常选中")

        # ── 3) Esc 语义不变：立即送回取消哨兵 ──
        got2 = {}
        screen2 = Screen("请选择操作", ["确认", "修改", "放弃"],
                         default="确认", body="计划正文", lang="chinese")
        app.push_screen(screen2, lambda r: got2.__setitem__("r", r))
        await pilot.pause()
        app.post_message(events.Key("escape", "\x1b"))
        await pilot.pause()
        assert got2.get("r") == m._CANCEL, f"Esc 应立即送回取消哨兵，实际 {got2.get('r')!r}"
        print("PASS 计划弹窗：Esc 立即送回取消哨兵（语义不变）")


def main():
    asyncio.run(_run())
    print("\nALL PASS")


if __name__ == "__main__":
    main()
