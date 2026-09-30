#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI TUI 补全 Tab 循环选择（无头真跑 Textual）。

背景（用户反馈）：
  TUI 里按 Tab 只会「一次性把第一个候选整词填进去」——因为 action_complete_accept
  接受候选后又调 _menu_update，把 menu.highlighted 复位成 0，于是连按 Tab 永远停在
  第一项，手感与主 REPL 不一致。

现行为（对齐主 REPL 的 completion_next / AI REPL 的 _complete_next）：
  · 菜单已开（TUI 里输入 / 或路径就会自动弹出）→ 每次 Tab 选中并插入**下一项**，
    到末尾环绕回第一项；
  · 菜单被关掉时（Esc）→ 首次 Tab 只重新打开菜单、不插入；
  · 用户手动改输入 → 循环复位，从候选[0] 重新开始。

本测试锁死这三条行为。

运行: python3 test/virtual/test_tui_tab_cycle.py
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
    from bin.ai_tui import _build_tui
    from bin.ai_interactive import completion_candidates

    App = _build_tui()
    app = App(session_kwargs={}, ctx={"lang": "chinese"})

    async with app.run_test(size=(120, 40)) as pilot:
        inp = app.query_one("#prompt")
        menu = app.query_one("#complete-menu")

        base = "/e"                       # /exit、/export → 两个候选，能验证环绕
        cands = completion_candidates(base, "chinese")
        assert len(cands) >= 2, f"需要一个至少有 2 个候选的前缀，实际 {cands!r}"

        def expect(cand):
            value, _meta, replace_len = cand
            return base[:max(0, len(base) - replace_len)] + value

        inp.focus()
        inp.value = base
        app._menu_update(base)
        await pilot.pause()
        await pilot.pause()
        assert menu.display is True, "输入 /e 后补全菜单应显示"

        # ① 第 1 次 Tab：选中并插入第 0 个候选
        await pilot.press("tab")
        await pilot.pause()
        await pilot.pause()
        assert inp.value == expect(cands[0]), f"第 1 次 Tab 应插入候选[0]，实际 {inp.value!r}"
        assert menu.highlighted == 0, f"高亮应为 0，实际 {menu.highlighted!r}"
        print(f"PASS 第 1 次 Tab：插入候选[0] {expect(cands[0])!r}")

        # ② 第 2 次 Tab：向下走到第 1 个候选（旧实现会原地不动）
        await pilot.press("tab")
        await pilot.pause()
        await pilot.pause()
        assert inp.value == expect(cands[1]), f"第 2 次 Tab 应插入候选[1]，实际 {inp.value!r}"
        assert menu.highlighted == 1, f"高亮应为 1，实际 {menu.highlighted!r}"
        print(f"PASS 第 2 次 Tab：向下选择候选[1] {expect(cands[1])!r}")

        # ③ 继续按 Tab 直到环绕回第 0 项
        for _ in range(len(cands) - 1):
            await pilot.press("tab")
            await pilot.pause()
            await pilot.pause()
        assert inp.value == expect(cands[0]), f"应环绕回候选[0]，实际 {inp.value!r}"
        assert menu.highlighted == 0, f"环绕后高亮应为 0，实际 {menu.highlighted!r}"
        print(f"PASS 环绕：{len(cands)} 个候选循环回候选[0]")

        # ④ 菜单关闭时首次 Tab 只打开菜单、不插入
        inp.value = base
        await pilot.pause()
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert menu.display is False, "Esc 后菜单应关闭"
        await pilot.press("tab")
        await pilot.pause()
        await pilot.pause()
        assert menu.display is True, "菜单关闭时 Tab 应重新打开菜单"
        assert inp.value == base, f"重开菜单的首次 Tab 不应插入，实际 {inp.value!r}"
        print("PASS 菜单关闭时：首次 Tab 只打开菜单、不插入")

        # ⑤ 用户手动改输入 → 循环复位
        inp.value = "/cl"          # 换成另一个前缀（值变了才会触发 Changed → _menu_update）
        await pilot.pause()
        await pilot.pause()
        assert app._menu_base_text is None and app._menu_cycle_idx == -1, \
            "用户编辑输入后补全循环应复位"
        assert inp.value == "/cl", f"用户编辑不应被补全改写，实际 {inp.value!r}"
        print("PASS 用户编辑后循环复位")


def main():
    asyncio.run(_run())
    print("\nALL PASS")


if __name__ == "__main__":
    main()
