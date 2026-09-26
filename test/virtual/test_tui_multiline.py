#!/usr/bin/env python3
"""TUI 多行输入回归（Alt+Enter 进多行 / 多行内 Enter 换行 / Alt+Enter 整体发送）。

根因（实测）：Textual 的 XTermParser 解析 ESC+CR（Alt+Enter）会**丢掉 alt 修饰符** ——
`feed("\\x1b\\r")` 先返回空，下一次 feed 才吐出裸 `enter` → 在单行框里等价于「直接发送」，
多行模式永远进不去。CSI-u 的 `\\x1b[13;2u` 则被稳定解析成 `shift+enter`。
故：字节层把 ESC+CR / ESC+LF 统一重写成 CSI-u，两个输入框各绑 shift+enter。

运行: python3 test/virtual/test_tui_multiline.py
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

from bin.ai_tui import (  # noqa: E402
    _InputSanitizer, _SanitizingDecoder, _ALT_ENTER_CSI_U, _enable_alt_enter_keys,
)


def test_bytes_rewritten():
    assert _InputSanitizer().feed(b"\x1b\r") == _ALT_ENTER_CSI_U, "ESC+CR 应改写为 CSI-u"
    assert _InputSanitizer().feed(b"\x1b\n") == _ALT_ENTER_CSI_U, "ESC+LF（ICRNL）同样改写"
    # 普通回车 / 其他键不受影响
    assert _InputSanitizer().feed(b"\r") == b"\r"
    assert _InputSanitizer().feed(b"ab\x1b[A") == b"ab\x1b[A"
    # 相邻内容也能正确改写
    assert _InputSanitizer().feed(b"x\x1b\ry") == b"x" + _ALT_ENTER_CSI_U + b"y"
    print("PASS 字节层：ESC+CR / ESC+LF → CSI-u shift+enter，其余按键不受影响")


def test_parser_yields_shift_enter():
    """走「字节 → 净化解码 → Textual 解析器」真实链路。"""
    _enable_alt_enter_keys()
    from textual._xterm_parser import XTermParser

    for raw in (b"\x1b\r", b"\x1b\n"):
        text = _SanitizingDecoder().decode(raw)
        keys = [k.key for k in XTermParser().feed(text)]
        assert keys == ["shift+enter"], f"{raw!r} 应解析为 shift+enter，实际 {keys}"

    # 对照组：未经改写的 ESC+CR 永远拿不到 shift+enter（旧行为就是「进不了多行」）
    p = XTermParser()
    raw_keys = [k.key for k in p.feed("\x1b\r")]
    raw_keys += [k.key for k in p.feed("")]
    assert "shift+enter" not in raw_keys, f"对照：未改写不应产生 shift+enter，实际 {raw_keys}"
    print(f"PASS 链路：改写后 → ['shift+enter']；未改写 → {raw_keys}（拿不到 shift+enter = 旧 bug）")


async def _run_app():
    from bin.ai_tui import _build_tui
    from textual.widgets import Input

    App = _build_tui()
    app = App(session_kwargs={}, ctx={"lang": "chinese"})
    async with app.run_test(size=(100, 30)) as pilot:
        sent = []
        app._run_one = lambda t: sent.append(t)
        app._hist_add = lambda t: None
        inp = app.query_one("#prompt", Input)
        ml = app.query_one("#prompt-ml")
        inp.focus()
        await pilot.pause()

        # 1) 单行框：普通 Enter 仍然是「发送」（不能被多行逻辑抢走）
        await pilot.press("h", "i")
        await pilot.press("enter")
        await pilot.pause()
        assert sent == ["hi"], f"普通 Enter 应发送，实际 {sent}"
        assert ml.display is False, "普通 Enter 不应进多行"
        print("PASS 单行框普通 Enter 仍是发送（未回归）")

        # 2) Alt+Enter（CSI-u）→ 进多行，并把已输入内容带过去
        await pilot.press("o", "k")
        await pilot.press("shift+enter")
        await pilot.pause()
        assert ml.display is True and inp.display is False, "Alt+Enter 应切到多行框"
        assert ml.text == "ok\n", f"已输入内容应带进多行框，实际 {ml.text!r}"
        assert sent == ["hi"], f"切多行不应发送，实际 {sent}"
        print("PASS Alt+Enter → 进多行（不发送）且带上已有内容")

        # 3) 多行框内：Enter 换行
        await pilot.press("a", "b")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        assert ml.text == "ok\nab\nc", f"多行框 Enter 应换行，实际 {ml.text!r}"
        assert sent == ["hi"], f"多行框 Enter 不应发送，实际 {sent}"
        print("PASS 多行框内 Enter 换行（不发送）")

        # 4) 多行框内 Alt+Enter → 整体发送并退回单行框
        await pilot.press("shift+enter")
        await pilot.pause()
        await pilot.pause()
        assert sent == ["hi", "ok\nab\nc"], f"Alt+Enter 应整体发送，实际 {sent}"
        assert ml.display is False and inp.display is True, "发送后应退回单行框"
        assert inp.value == "", "发送后单行框应清空"
        print(f"PASS 多行 Alt+Enter 整体发送：{sent[-1]!r}")

        # 5) 空多行内容不发送，也不报错
        inp.value = ""
        await pilot.press("shift+enter")
        await pilot.pause()
        assert ml.display is True
        await pilot.press("shift+enter")
        await pilot.pause()
        assert len(sent) == 2, f"空内容不应发送，实际 {sent}"
        print("PASS 空多行内容不发送")


async def _run_visibility():
    """回归：多行框里的字必须真的出现在屏幕上（高度要把边框算进去）。

    旧 bug：`_sync_height` 把 `styles.height` 设成「显示行数」，而 `styles.height`
    是**含边框**的总高 —— round 边框吃掉 2 行 → 内容区被压成 0/负数：
      · 1~2 行时整个框是空的，用户敲进去的字全都看不见；
      · 行数多时只看得见最上面几行，正在输入的最后几行被裁掉。
    """
    from bin.ai_tui import _build_tui
    from textual.widgets import Input

    App = _build_tui()
    app = App(session_kwargs={}, ctx={"lang": "chinese"})
    async with app.run_test(size=(80, 24)) as pilot:
        inp = app.query_one("#prompt", Input)
        ml = app.query_one("#prompt-ml")
        inp.focus()
        await pilot.pause()

        def screen_text() -> str:
            strips = app.screen._compositor.render_strips()
            return "\n".join("".join(seg.text for seg in s) for s in strips)

        # 1) 2 行：内容区必须真有 2 行，两行文字都要在屏幕上
        await pilot.press("shift+enter")
        await pilot.pause()
        ml.text = "hello\nworld"
        await pilot.pause()
        assert ml.content_size.height == 2, \
            f"2 行内容区应=2（边框不算内容），实际 {ml.content_size.height}"
        screen = screen_text()
        assert "hello" in screen and "world" in screen, \
            f"2 行文本必须可见，实际屏幕：\n{screen}"
        print("PASS 2 行文本真实可见（内容区高度已扣除边框）")

        # 2) 6 行：折叠成 上2 + 省略 + 下2，且用户正在输入的末两行必须可见
        ml.text = "L1\nL2\nL3\nL4\nL5\nL6"
        await pilot.pause()
        rows = len(ml._row_map())
        assert ml.content_size.height == rows, \
            f"内容区高度应={rows}（显示行数），实际 {ml.content_size.height}"
        assert ml.outer_size.height == rows + 2, \
            f"总高应={rows}+2（上下边框），实际 {ml.outer_size.height}"
        screen = screen_text()
        assert "L1" in screen, f"首行必须可见，实际：\n{screen}"
        assert "L5" in screen and "L6" in screen, f"末两行（正在输入）必须可见，实际：\n{screen}"
        assert "省略" in screen, f"折叠时必须出现省略提示行，实际：\n{screen}"
        # 滚动条滑块不应出现在文字区里（折叠由控件自管）
        assert not any(ch in screen for ch in "▅▃▊"), f"不应出现滚动条滑块，实际：\n{screen}"
        print("PASS 6 行折叠：首行 + 省略提示 + 末两行都在屏幕上，且无滚动条滑块")


def main():
    test_bytes_rewritten()
    test_parser_yields_shift_enter()
    asyncio.run(_run_app())
    asyncio.run(_run_visibility())
    print("\nALL PASS")


if __name__ == "__main__":
    main()
