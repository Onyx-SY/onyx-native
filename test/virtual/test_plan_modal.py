#!/usr/bin/env python3
"""计划确认弹窗回归：正文与询问**同图层**、正文可滚动、屏幕自适应、不重复打印。

旧问题：计划先 console.print 到主屏，再压一个只有 Static+OptionList 的模态框，
正文被 70% 遮罩盖住且无滚动容器（超长计划直接被裁剪）。

运行: python3 test/virtual/test_plan_modal.py
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


LONG_PLAN = "\n".join(f"步骤 {i}：这是计划正文的第 {i} 行，用来撑出滚动条。" for i in range(60))
OPTIONS = ["✅ 确认计划，开始执行", "💡 提出修改意见", "🗑️ 摒弃计划，重新制定"]


async def _run(size, body, tag, expect_scroll=True):
    from bin.ai_tui import _build_tui
    import bin.ai_tui as _t
    from textual.screen import ModalScreen
    from textual.containers import VerticalScroll
    from textual.widgets import OptionList

    App = _build_tui()
    Screen = _t._TUI_PLAN_SCREEN
    assert Screen is not None, "PlanConfirmScreen 未导出"

    app = App(session_kwargs={}, ctx={"lang": "chinese"})
    async with app.run_test(size=size) as pilot:
        box = {}

        def cb(res):
            box["r"] = res

        app.push_screen(Screen("请选择操作:", list(OPTIONS), OPTIONS[0],
                               body=body, lang="chinese"), cb)
        await pilot.pause()
        modal = app.screen
        assert isinstance(modal, ModalScreen), f"应推入 ModalScreen，实际 {type(modal)}"
        wrap = modal.query_one("#modal")
        vs = modal.query_one("#modal-body", VerticalScroll)
        ol = modal.query_one("#modal-list", OptionList)

        # 1) 正文与选项同处一个弹窗（同一个 #modal 容器 = 同一图层）
        assert vs.parent is wrap and ol.parent is wrap, "正文与选项应在同一个弹窗容器内"
        print(f"PASS[{tag}] 正文与选项同图层（#modal 内）")

        # 2) 弹窗不超出屏幕
        assert modal.size.height <= size[1] and modal.size.width <= size[0]
        reg = wrap.region
        assert reg.y >= 0 and reg.y + reg.height <= size[1], f"弹窗超出屏幕：{reg} vs {size}"
        print(f"PASS[{tag}] 弹窗不越界（{size[0]}×{size[1]}，modal={reg}）")

        # 3) 长计划可滚动；正文区高度随屏幕自适应（不小于 3 行）
        assert vs.size.height >= 3, f"正文区过矮：{vs.size.height}"
        assert ol.display is True and ol.region.height >= 1, "选项列表应可见"
        if expect_scroll:
            assert vs.max_scroll_y > 0, "长计划应可滚动"
            print(f"PASS[{tag}] 正文区可滚动（height={vs.size.height}, max_scroll_y={vs.max_scroll_y}）")
        else:
            assert vs.max_scroll_y == 0, "短计划不该有滚动条"
            print(f"PASS[{tag}] 短计划不出现滚动条（height={vs.size.height}）")

        # 4) PageDown / PageUp 滚动正文（短计划时是空操作，不应报错）
        modal.action_body_down()
        await pilot.pause()
        if expect_scroll:
            assert vs.scroll_y > 0, "PageDown 应滚动正文"
        modal.action_body_up()
        await pilot.pause()
        assert vs.scroll_y == 0, "PageUp 应滚回顶部"
        print(f"PASS[{tag}] PageUp/PageDown 滚动正文")

        # 5) 选择 → dismiss 选项文本
        # ⚠️ 弹窗挂载后有 _MODAL_KEY_GRACE(0.30s) 宽限窗口：窗口内的**键盘**选择被忽略
        #    （防「计划还没看完就被回车/残留按键确认」），这里必须先等过窗口。
        await asyncio.sleep(0.35)
        ol.focus()
        await pilot.press("enter")
        await pilot.pause()
        assert box.get("r") == OPTIONS[0], f"选择结果异常：{box.get('r')!r}"
        print(f"PASS[{tag}] 选中返回选项文本：{box.get('r')!r}")

        # 6) Esc → 取消哨兵（绝不能当成默认项「确认」）
        box2 = {}
        app.push_screen(Screen("请选择操作:", list(OPTIONS), OPTIONS[0],
                               body=body, lang="chinese"), lambda r: box2.update(r=r))
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert box2.get("r") == "__cancel__", f"Esc 应返回取消哨兵，实际 {box2.get('r')!r}"
        print(f"PASS[{tag}] Esc 返回取消哨兵（不会误确认）")


def test_confirm_plan_tui_no_duplicate_print():
    """TUI 下 confirm_plan 不再把计划 print 到主屏，而是作为 body 传给弹窗。"""
    from bin.ai_lib import helpers, mode

    printed = []
    captured = {}
    orig_console, orig_select = helpers.console, helpers.select_option
    old_mode = mode.resolve_ai_mode
    try:
        mode.set_render_mode("tui")

        class _C:
            def print(self, *a, **k):
                printed.append(a)

        def _fake_select(**kw):
            captured.update(kw)
            return kw["options"][0]

        helpers.console = _C()
        helpers.select_option = _fake_select
        r = helpers.confirm_plan("## 计划\n1. 第一步", {
            "plan_opt_confirm": "确认", "plan_opt_guide": "修改", "plan_opt_discard": "摒弃",
            "plan_prompt": "请选择操作:",
        })
    finally:
        helpers.console, helpers.select_option = orig_console, orig_select
        mode.set_render_mode("repl")

    assert r == "confirm", f"返回值异常：{r!r}"
    assert printed == [], f"TUI 下不应再 print 计划（会重复且被遮罩压暗）：{printed}"
    assert captured.get("body") == "## 计划\n1. 第一步", "计划正文应作为 body 传给弹窗"
    print("PASS TUI 下不重复打印计划，正文经 body 进入弹窗")

    # REPL 下仍然打印（保持原行为）
    printed.clear()
    try:
        helpers.console = type("C", (), {"print": lambda self, *a, **k: printed.append(a)})()
        helpers.select_option = lambda **kw: kw["options"][0]
        from bin.ai_lib import mode as _m
        _m.set_render_mode("repl")
        helpers.confirm_plan("PLAN", {"plan_opt_confirm": "确认", "plan_opt_guide": "修改",
                                      "plan_opt_discard": "摒弃", "plan_prompt": "?"})
    finally:
        helpers.console, helpers.select_option = orig_console, orig_select
        mode.set_render_mode("repl")
    assert printed, "REPL 下应仍然打印计划面板"
    print("PASS REPL 下仍打印计划面板（行为不变）")


def main():
    test_confirm_plan_tui_no_duplicate_print()
    asyncio.run(_run((80, 24), LONG_PLAN, "80x24"))
    asyncio.run(_run((40, 20), LONG_PLAN, "40x20"))
    asyncio.run(_run((120, 40), "短计划一行", "120x40", expect_scroll=False))
    print("\nALL PASS")


if __name__ == "__main__":
    main()
