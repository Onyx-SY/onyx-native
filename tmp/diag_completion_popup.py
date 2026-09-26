# -*- coding: utf-8 -*-
"""诊断：为什么改后「边打边弹补全列表」不见了。"""
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput


class Tiny(Completer):
    def get_completions(self, document, complete_event):
        for w in ("git", "grep", "ls"):
            if w.startswith(document.text_before_cursor):
                yield Completion(w, start_position=-len(document.text_before_cursor))


async def probe(sess, label, typed="gi"):
    with create_pipe_input() as inp:
        sess.app.input = inp
        sess.app.output = DummyOutput()
        task = asyncio.ensure_future(sess.prompt_async("> "))
        await asyncio.sleep(0.15)
        inp.send_bytes(typed.encode())
        await asyncio.sleep(0.6)
        cs = sess.default_buffer.complete_state
        n = len(cs.completions) if cs else 0
        print(f"{label:28} 输入 {typed!r} → complete_state={'有' if cs else '无'} / 候选 {n}")
        inp.send_bytes(b"\r")
        try:
            await asyncio.wait_for(task, timeout=2)
        except Exception:
            pass


async def main():
    print("=== ① 纯 prompt_toolkit 基线（只给 Tiny 补全器）===")
    await probe(PromptSession(completer=Tiny(), complete_while_typing=True), "基线")

    print("\n=== ② 新输入层 build_session ===")
    from lib.terminal.repl import build_session
    s = build_session(commands=["git", "grep", "ls"])
    print("   complete_while_typing =", s.complete_while_typing)
    print("   completer             =", type(s.completer).__name__)
    print("   complete_style        =", s.complete_style)
    await probe(s, "新输入层")

    print("\n=== ③ 新输入层但显式传 complete_while_typing ===")
    s2 = PromptSession(completer=Tiny(), complete_while_typing=True,
                       style=s.style, key_bindings=s.key_bindings,
                       bottom_toolbar=s.bottom_toolbar)
    await probe(s2, "新键位+新样式")

    print("\n=== ④ 旧 input_lib 的 completion_typing_filter 是什么 ===")
    import lib.terminal.input_lib as IL
    print("   ", repr(getattr(IL, "completion_typing_filter", "（无此全局）")))
    import inspect
    src = inspect.getsource(IL)
    for ln in src.splitlines():
        if "completion_typing_filter" in ln:
            print("   源码:", ln.strip()[:110])


asyncio.run(main())
