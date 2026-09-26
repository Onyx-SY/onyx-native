#!/usr/bin/env python3
"""健壮性与美观回归：补全菜单复活（重复 on_input_changed）/ 错误块只打一次 / 回复留白。

运行: python3 test/virtual/test_tui_stage5.py
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


def test_single_on_input_changed():
    """OnyxTUI 里只能有一份 on_input_changed（旧实现两份 → 后者覆盖前者，补全菜单失效）。"""
    src = open(os.path.join(ROOT, "bin", "ai_tui.py"), encoding="utf-8").read()
    head = src.index("class OnyxTUI")
    body = src[head:]
    # 类体到 _TUI_APP_CLASS 赋值之前
    end = body.index("    _TUI_APP_CLASS")
    body = body[:end]
    n = body.count("def on_input_changed")
    assert n == 1, f"OnyxTUI 内 on_input_changed 应只有 1 份，实际 {n} 份"
    assert "_menu_update(event.value)" in body, "补全菜单刷新逻辑丢失"
    assert "_hist_idx = len(self._hist)" in body, "历史游标复位逻辑丢失"
    print("PASS OnyxTUI 内 on_input_changed 唯一，且同时保留补全刷新与历史复位")


def test_error_block_printed_once():
    src = open(os.path.join(ROOT, "bin", "ai_cmd.py"), encoding="utf-8").read()
    assert "_tui_write_rich(tui_plain(_err_body" in src, "TUI 错误应以角色标签块输出"
    i = src.index("elif _tui_mode and has_error:")
    seg = src[i:i + 400]
    assert "console.print(tui_plain(f\"❌" not in seg, "TUI 错误不应重复打印"
    assert "pass" in seg.split("\n")[3] or "pass" in seg, "重复分支应为空操作"
    print("PASS TUI 错误块只打印一次（旧的重复分支已置空）")


def test_reply_gets_trailing_blank():
    src = open(os.path.join(ROOT, "bin", "ai_cmd.py"), encoding="utf-8").read()
    assert "_tui_write_rich(\"\")" in src, "TUI 回复块之后应留一行空白"
    print("PASS TUI 回复块后留白（块间距统一）")


async def _run_completion():
    from bin.ai_tui import _build_tui
    from textual.widgets import Input

    App = _build_tui()
    app = App(session_kwargs={}, ctx={"lang": "chinese"})
    async with app.run_test(size=(100, 30)) as pilot:
        menu = app.query_one("#complete-menu")
        inp = app.query_one("#prompt", Input)
        inp.focus()
        await pilot.pause()
        assert menu.display is False, "初始不应显示补全菜单"

        # 打字（不是按 Tab）就应该弹出补全菜单 —— 旧实现两份 on_input_changed 互相覆盖，
        # 这里永远弹不出来（只有 Tab 才行）。
        await pilot.press("/")
        await pilot.pause()
        assert inp.value == "/", f"输入异常：{inp.value!r}"
        assert menu.display is True, "输入 / 后补全菜单应自动弹出（不需要按 Tab）"
        assert app._menu_cands, "补全候选为空"
        print(f"PASS 输入即弹补全菜单（{len(app._menu_cands)} 个候选，无需按 Tab）")

        # 历史游标复位仍然生效（合并后不能丢）
        app._hist = ["old-1", "old-2"]
        app._hist_idx = 0
        await pilot.press("a")
        await pilot.pause()
        assert app._hist_idx == len(app._hist), "输入后历史游标应回到最新"
        print("PASS 输入后历史游标回到最新（合并未丢行为）")

        # 清空 → 菜单收起
        for _ in range(len(inp.value)):
            await pilot.press("backspace")
        await pilot.pause()
        assert menu.display is False, "空输入时补全菜单应收起"
        print("PASS 输入清空后补全菜单收起")


def main():
    test_single_on_input_changed()
    test_error_block_printed_once()
    test_reply_gets_trailing_blank()
    asyncio.run(_run_completion())
    print("\nALL PASS")


if __name__ == "__main__":
    main()
