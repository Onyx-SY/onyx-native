#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复现：TUI 单行输入框按 Enter（真实终端路径 character="\\r"）无法发送。

用 post_message 直接投递 events.Key，绕过 Pilot.press（后者构造的 Key character=None，
复现不出终端真实路径）。
"""
import asyncio
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


async def _run():
    from bin.ai_tui import _build_tui
    from textual import events

    home = tempfile.mkdtemp()
    App = _build_tui()
    app = App(session_kwargs={"user_home_dir": home}, ctx={"lang": "chinese"})
    async with app.run_test(size=(100, 30)) as pilot:
        app._stop.set()          # 停掉 worker，避免真的去跑 AI
        await pilot.pause()
        inp = app.query_one("#prompt")
        inp.focus()
        await pilot.pause()

        # ── 1) Enter 发送（终端真实编码：character="\r"）──
        inp.value = "hello"
        await pilot.pause()
        app.post_message(events.Key("enter", "\r"))
        await pilot.pause()
        qsize = app._in_q.qsize()
        print(f"[enter] queue={qsize} value={inp.value!r}")
        assert qsize == 1, "❌ Enter 未提交（被输入框吞掉了）"
        assert inp.value == "", f"❌ 提交后输入框未清空：{inp.value!r}"

        # ── 2) 退格（character="\x7f"）──
        inp.value = "abc"
        inp.cursor_position = len(inp.value)
        await pilot.pause()
        app.post_message(events.Key("backspace", "\x7f"))
        await pilot.pause()
        print(f"[backspace] value={inp.value!r}")
        assert inp.value == "ab", f"❌ 退格失效：{inp.value!r}"

        # ── 3) 可打印字符仍能输入 ──
        inp.value = ""
        await pilot.pause()
        app.post_message(events.Key("x", "x"))
        await pilot.pause()
        print(f"[printable] value={inp.value!r}")
        assert inp.value == "x", f"❌ 普通字符输入失效：{inp.value!r}"

        # ── 4) 转义泄漏窗口：Esc 后紧跟的可打印字节应被丢弃 ──
        app.post_message(events.Key("escape", "\x1b"))
        await pilot.pause()
        app.post_message(events.Key("5", "5"))
        await pilot.pause()
        print(f"[esc-leak] value={inp.value!r}")
        assert inp.value == "x", f"❌ 转义泄漏未过滤：{inp.value!r}"

    print("\nALL PASS")


if __name__ == "__main__":
    asyncio.run(_run())
