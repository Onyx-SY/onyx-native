#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归测试：TUI 输入框的按键通路（Enter 发送 / 退格 / 转义泄漏防护 / Alt+Enter 多行）。

背景（真实 bug）：单行输入框的 _on_key 曾把「character 是控制字节」的按键一律吞掉，
于是 Enter(\\r)、退格(\\x7f)、Tab 全部失效 —— 按 Enter 完全无法发送。
同时该防护的「Esc 泄漏窗口」永远打不开（Esc 被 priority 绑定提前消费），防护形同虚设。

本测试用 post_message(events.Key(...)) 投递「终端真实编码」的按键
（Pilot.press 构造的 Key character=None，复现不出该 bug）：
  - Enter  → key="enter",  character="\\r"
  - 退格   → key="backspace", character="\\x7f"
  - 泄漏   → key="escape" 之后紧跟可打印字节（滑动/滚轮场景）

运行: python3 test/virtual/test_tui_enter_submit.py
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


async def _run():
    from bin.ai_tui import _build_tui
    from textual import events

    App = _build_tui()
    app = App(session_kwargs={"user_home_dir": tempfile.mkdtemp()}, ctx={"lang": "chinese"})
    async with app.run_test(size=(100, 30)) as pilot:
        app._stop.set()          # 停掉 worker/drain，避免 _submit 真的去跑 AI
        await pilot.pause()
        inp = app.query_one("#prompt")
        inp.focus()
        await pilot.pause()

        # ── 1) Enter 发送（终端真实编码 character="\r"）──
        inp.value = "hello"
        await pilot.pause()
        app.post_message(events.Key("enter", "\r"))
        await pilot.pause()
        assert app._in_q.qsize() == 1, "❌ Enter 未提交（按键被吞）"
        assert inp.value == "", f"❌ 提交后输入框未清空：{inp.value!r}"
        print("PASS Enter 发送并清空输入框")

        # ── 2) 空内容 Enter 不入队 ──
        inp.value = ""
        await pilot.pause()
        app.post_message(events.Key("enter", "\r"))
        await pilot.pause()
        assert app._in_q.qsize() == 1, "❌ 空内容 Enter 不应入队"
        print("PASS 空内容 Enter 不入队")

        # ── 3) 退格（character="\x7f"）──
        inp.value = "abc"
        inp.cursor_position = len(inp.value)
        await pilot.pause()
        app.post_message(events.Key("backspace", "\x7f"))
        await pilot.pause()
        assert inp.value == "ab", f"❌ 退格失效：{inp.value!r}"
        print("PASS 退格可用")

        # ── 4) 普通可打印字符正常输入 ──
        inp.value = ""
        await pilot.pause()
        app.post_message(events.Key("x", "x"))
        await pilot.pause()
        assert inp.value == "x", f"❌ 普通字符输入失效：{inp.value!r}"
        print("PASS 普通字符输入正常")

        # ── 5) 转义泄漏：Esc 后紧跟的可打印字节被丢弃 ──
        app.post_message(events.Key("escape", "\x1b"))
        await pilot.pause()
        assert app._esc_until > 0, "❌ Esc 未打开泄漏窗口（防护失效）"
        for c in "[<35;12;5M":
            app.post_message(events.Key(c, c))
        await pilot.pause()
        assert inp.value == "x", f"❌ 转义泄漏未过滤：{inp.value!r}"
        print("PASS 转义泄漏字节被丢弃")

        # ── 6) 窗口过期后输入恢复正常 ──
        await asyncio.sleep(0.15)
        app.post_message(events.Key("z", "z"))
        await pilot.pause()
        assert inp.value == "xz", f"❌ 窗口外输入被误伤：{inp.value!r}"
        print("PASS 泄漏窗口过期后输入正常")

        # ── 7) Enter 仍可发送（泄漏窗口逻辑不得影响控制键）──
        app.post_message(events.Key("escape", "\x1b"))   # 重新打开窗口
        await pilot.pause()
        app.post_message(events.Key("enter", "\r"))
        await pilot.pause()
        assert app._in_q.qsize() == 2, "❌ 泄漏窗口内 Enter 被误吞"
        print("PASS 泄漏窗口内 Enter 照常发送")

        # ── 8) Alt+Enter：单行 → 多行；多行内 Alt+Enter → 发送并回到单行 ──
        inp.value = ""
        await pilot.pause()
        app.post_message(events.Key("a", "a"))
        app.post_message(events.Key("alt+enter", "\r"))
        await pilot.pause()
        ml = app.query_one("#prompt-ml")
        assert ml.display is True, "❌ Alt+Enter 未切到多行框"
        assert inp.display is False, "❌ 切多行后单行框应隐藏"
        app.post_message(events.Key("b", "b"))
        app.post_message(events.Key("enter", "\r"))      # 多行内 Enter = 换行
        await pilot.pause()
        assert "\n" in (ml.text or ""), f"❌ 多行内 Enter 应换行：{ml.text!r}"
        app.post_message(events.Key("alt+enter", "\r"))  # 多行内 Alt+Enter = 发送
        await pilot.pause()
        assert app._in_q.qsize() == 3, f"❌ 多行 Alt+Enter 未发送：{app._in_q.qsize()}"
        assert ml.display is False and inp.display is True, "❌ 发送后未回到单行框"
        print("PASS Alt+Enter 多行往返（Enter 换行 / Alt+Enter 发送）")

        # ── 9) Ctrl+C 绑定仍在（闸门不得吞掉控制键）──
        handled = await app._check_bindings("ctrl+c", priority=True)
        assert handled is True, "❌ Ctrl+C 绑定失效"
        print("PASS Ctrl+C 绑定仍在")


def _test_sanitizer_bytes():
    """字节层：换行归一化（LF/CRLF→CR）+ Alt+Enter + 粘贴保真 + 非法字节/鼠标剔除。"""
    from bin.ai_tui import _InputSanitizer

    def feed(chunks):
        s = _InputSanitizer(strip_mouse=True)
        out = b""
        for c in chunks:
            out += s.feed(c)
        return out

    # Enter 编码归一化：CR / LF / CRLF 一律 → CR
    assert feed([b"\r"]) == b"\r"
    assert feed([b"\n"]) == b"\r", "LF 必须归一化为 CR（否则 Textual 给 ctrl+j，Enter 失灵）"
    assert feed([b"\r\n"]) == b"\r", "CRLF 必须折叠为单个 CR（否则多行框多插一个换行）"
    # Alt+Enter：ESC+CR / ESC+LF → CSI-u shift+enter
    assert feed([b"\x1b\r"]) == b"\x1b[13;2u"
    assert feed([b"\x1b\n"]) == b"\x1b[13;2u"
    # 分块送达（ESC 与 CR 被切开）：保持原样，交给 Textual 的 ESCAPE_DELAY 兜底
    assert feed([b"\x1b", b"\r"]) == b"\x1b\r"
    # 括号粘贴：内容里的 \n 原样保留（不得被当成 Enter 提交）
    assert feed([b"\x1b[200~a\nb\x1b[201~"]) == b"\x1b[200~a\nb\x1b[201~"
    # 中文保留；非法 UTF-8 首字节丢弃
    assert feed(["中文".encode("utf-8")]) == "中文".encode("utf-8")
    assert feed([b"\xbc"]) == b""
    # 鼠标报文剔除
    assert feed([b"\x1b[<35;12;5M"]) == b""
    print("PASS 字节层：换行归一化 / Alt+Enter / 粘贴保真 / 非法字节丢弃 / 鼠标剔除")


def main():
    _test_sanitizer_bytes()
    asyncio.run(_run())
    print("\nALL PASS")


if __name__ == "__main__":
    main()
