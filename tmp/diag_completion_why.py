# -*- coding: utf-8 -*-
"""定位：enable_history_search 到底挡住了哪一步。"""
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

calls = {"n": 0}


class Tiny(Completer):
    def get_completions(self, document, complete_event):
        calls["n"] += 1
        for w in ("git", "grep", "ls"):
            if w.startswith(document.text_before_cursor):
                yield Completion(w, start_position=-len(document.text_before_cursor))


async def probe(**kw):
    calls["n"] = 0
    sess = PromptSession(completer=Tiny(), complete_while_typing=True, **kw)
    with create_pipe_input() as inp:
        sess.app.input = inp
        sess.app.output = DummyOutput()
        task = asyncio.ensure_future(sess.prompt_async("> "))
        await asyncio.sleep(0.15)
        inp.send_bytes(b"gi")
        await asyncio.sleep(0.6)
        b = sess.default_buffer
        cs = b.complete_state
        print(f"   history_search={kw.get('enable_history_search', False)!s:5} "
              f"text={b.text!r} completer调用={calls['n']} "
              f"complete_state={'有' if cs else '无'} "
              f"history_search_text={b.history_search_text!r} "
              f"cwt={bool(b.complete_while_typing())}")
        inp.send_bytes(b"\r")
        try:
            await asyncio.wait_for(task, timeout=2)
        except Exception:
            pass


async def main():
    print("=== 对照 ===")
    await probe()
    await probe(enable_history_search=True)
    print("\n=== 关掉 search 绑定但保留 flag（看是不是绑定导致的）===")
    await probe(enable_history_search=True, key_bindings=None)


asyncio.run(main())
