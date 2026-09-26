#!/usr/bin/env python3
"""危险命令确认框回归：回车绝不能等于「确认」。

背景（用户实测 + 无头复现）：
  ConfirmScreen 弹出后默认焦点落在「是」按钮，而 Textual 的 Button 自带 `enter → press`，
  于是弹窗瞬间到达的残留回车（或用户「按回车关掉弹窗」的习惯动作）被「是」吃掉 →
  等价于「未经确认就放行」：危险命令直接执行完、结果回传 AI。

修复后必须成立：
  ① 弹窗后**立即**按 Enter → 不得确认；
  ② `default=False`（危险确认）→ 焦点在「否」，宽限期后按 Enter 也只能「拒绝」；
  ③ 只有显式 `y` / 点击「是」才算确认；`n` / `escape` = 拒绝；
  ④ CaptchaScreen 弹窗后立即按 Enter 不得被当成提交。

运行: python3 test/virtual/test_confirm_grace.py
"""
import asyncio
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import bin.ai_tui as tui  # noqa: E402


def _new_app():
    App = tui._build_tui()
    app = App({"user_home_dir": tempfile.mkdtemp(prefix="onyx_cf_")},
              {"lang": "chinese", "session_id": "t", "memory_mode": "global",
               "cwd": os.getcwd()})
    app._submit = lambda text: None
    return app


def _open(app, screen, box):
    app.push_screen(screen, lambda v: box.__setitem__("v", v))


async def test_confirm_enter_never_confirms():
    from textual.widgets import Button

    app = _new_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.2)

        # ① 弹窗后立即按 Enter（模拟残留回车）→ 不得确认
        box = {}
        _open(app, tui._TUI_CONFIRM_SCREEN("危险命令：rm -rf / 确认执行？", False, "chinese"), box)
        await pilot.pause(0.02)
        await pilot.press("enter")
        await pilot.pause(0.15)
        assert box.get("v") is None, f"宽限期内按 Enter 不该关闭弹窗，实际 {box.get('v')}"

        # ② 焦点必须在安全侧（default=False → 「否」）
        scr = app.screen
        focused = app.focused
        assert isinstance(focused, Button) and focused.id == "no", \
            f"危险确认的默认焦点应在「否」，实际 {getattr(focused, 'id', focused)}"

        # ③ 宽限期后再按 Enter → 也只能「拒绝」
        await pilot.pause(0.4)
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert box.get("v") is False, f"default=False 时 Enter 必须是拒绝，实际 {box.get('v')}"
    print("PASS 危险确认：残留回车不生效；焦点在「否」；Enter 只能拒绝")


async def test_confirm_explicit_yes_no():
    app = _new_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.2)

        # 显式 y（过宽限）→ 确认
        box = {}
        _open(app, tui._TUI_CONFIRM_SCREEN("确认执行？", False, "chinese"), box)
        await pilot.pause(0.45)
        await pilot.press("y")
        await pilot.pause(0.2)
        assert box.get("v") is True, f"显式 y 应确认，实际 {box.get('v')}"

        # n → 拒绝
        box2 = {}
        _open(app, tui._TUI_CONFIRM_SCREEN("确认执行？", True, "chinese"), box2)
        await pilot.pause(0.45)
        await pilot.press("n")
        await pilot.pause(0.2)
        assert box2.get("v") is False, box2

        # escape → 拒绝
        box3 = {}
        _open(app, tui._TUI_CONFIRM_SCREEN("确认执行？", True, "chinese"), box3)
        await pilot.pause(0.45)
        await pilot.press("escape")
        await pilot.pause(0.2)
        assert box3.get("v") is False, box3
    print("PASS 显式 y=确认；n / escape=拒绝")


async def test_confirm_click():
    from textual.widgets import Button

    app = _new_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.2)

        # 宽限期内点「是」不生效
        box = {}
        _open(app, tui._TUI_CONFIRM_SCREEN("确认执行？", False, "chinese"), box)
        await pilot.pause(0.05)
        app.screen.query_one("#yes", Button).press()
        await pilot.pause(0.15)
        assert box.get("v") is None, f"宽限期内点「是」不该生效，实际 {box.get('v')}"

        # 过宽限后点「是」→ 确认
        await pilot.pause(0.4)
        app.screen.query_one("#yes", Button).press()
        await pilot.pause(0.2)
        assert box.get("v") is True, box
    print("PASS 点击「是」：宽限期内无效、宽限后确认")


async def test_default_true_focuses_yes():
    from textual.widgets import Button

    app = _new_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.2)
        box = {}
        _open(app, tui._TUI_CONFIRM_SCREEN("继续？", True, "chinese"), box)
        await pilot.pause(0.45)
        focused = app.focused
        assert isinstance(focused, Button) and focused.id == "yes", \
            f"default=True 时焦点应在「是」，实际 {getattr(focused, 'id', focused)}"
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert box.get("v") is True, box
    print("PASS default=True 保留便捷语义（焦点「是」，Enter 确认）")


async def test_captcha_grace():
    app = _new_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.2)
        box = {}
        _open(app, tui._TUI_CAPTCHA_SCREEN("🛡️ 危险命令确认", "警告", "ABCD", "chinese"), box)
        await pilot.pause(0.02)
        await pilot.press("enter")            # 残留回车
        await pilot.pause(0.15)
        assert box.get("v") is None, f"验证码框宽限期内回车不该被当作提交，实际 {box.get('v')}"
        # 空输入 + 回车（过宽限）→ 验证失败 = 拒绝
        await pilot.pause(0.4)
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert box.get("v") is False, f"空验证码必须拒绝，实际 {box.get('v')}"
    print("PASS 验证码框：宽限期内回车不提交；空码 = 拒绝")


async def test_session_exempt_not_over_force_confirm():
    """会话级豁免不得越过强制确认类型（rm / 重定向 / here-doc）。"""
    import lib.safe as safe
    src = open(os.path.join(ROOT, "lib", "safe.py"), encoding="utf-8").read()
    assert "if _SESSION_CAPTCHA_VERIFIED and not force_confirm:" in src, \
        "会话级豁免仍会越过强制确认类型"
    # 语义校验：豁免分支不应在 force_confirm 之前无条件返回 True
    idx = src.index("if _SESSION_CAPTCHA_VERIFIED and not force_confirm:")
    seg = src[idx:idx + 300]
    assert "return True" in seg, "豁免分支应返回 True（确认）"
    print("PASS 会话级豁免：强制确认类型（rm/重定向/here-doc）不再被豁免跳过")


async def _run_all_async():
    await test_confirm_enter_never_confirms()
    await test_confirm_explicit_yes_no()
    await test_confirm_click()
    await test_default_true_focuses_yes()
    await test_captcha_grace()
    await test_session_exempt_not_over_force_confirm()


def main():
    asyncio.run(_run_all_async())
    print("\nALL PASS")


if __name__ == "__main__":
    main()
