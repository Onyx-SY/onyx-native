# -*- coding: utf-8 -*-
"""验证修复：enable_history_search=True 会**故意**关掉 complete_while_typing。

prompt_toolkit 源码 shortcuts/prompt.py:517-526：
    complete_while_typing=Condition(
        lambda: is_true(self.complete_while_typing)
        and not is_true(self.enable_history_search)      # ← 就是这里
        and not self.complete_style == CompleteStyle.READLINE_LIKE)
所以「开了 Ctrl+R 搜索」= 「边打边弹补全被关掉」。修法：构造后把 default_buffer 的
该 filter 覆写回 True，Ctrl+R 依旧保留。
"""
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.filters import Condition
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput


class Tiny(Completer):
    def get_completions(self, document, complete_event):
        for w in ("git", "grep", "git status"):
            if w.startswith(document.text_before_cursor):
                yield Completion(w, start_position=-len(document.text_before_cursor))


async def probe(label, patch_buffer, keys=b"gi", wait=0.6):
    sess = PromptSession(completer=Tiny(), complete_while_typing=True,
                         enable_history_search=True)
    if patch_buffer:
        sess.default_buffer.complete_while_typing = Condition(lambda: True)
    with create_pipe_input() as inp:
        sess.app.input = inp
        sess.app.output = DummyOutput()
        task = asyncio.ensure_future(sess.prompt_async("> "))
        await asyncio.sleep(0.15)
        inp.send_bytes(keys)
        await asyncio.sleep(wait)
        b = sess.default_buffer
        cs = b.complete_state
        n = len(cs.completions) if cs else 0
        print(f"{label:26} → 菜单 {'有(%d)' % n if cs else '无'}  "
              f"cwt={bool(b.complete_while_typing())}  text={b.text!r}")
        inp.send_bytes(b"\r")
        try:
            await asyncio.wait_for(task, timeout=2)
        except Exception:
            pass


async def check_ctrl_r():
    """确认打补丁后 Ctrl+R 增量搜索仍可用。"""
    from prompt_toolkit.history import InMemoryHistory
    sess = PromptSession(completer=Tiny(), complete_while_typing=True,
                         enable_history_search=True,
                         history=InMemoryHistory(["git status", "git push", "ls -la"]))
    sess.default_buffer.complete_while_typing = Condition(lambda: True)
    with create_pipe_input() as inp:
        sess.app.input = inp
        sess.app.output = DummyOutput()
        task = asyncio.ensure_future(sess.prompt_async("> "))
        await asyncio.sleep(0.2)
        inp.send_bytes(b"\x12")          # Ctrl+R
        await asyncio.sleep(0.3)
        inp.send_bytes(b"push")          # 搜索词
        await asyncio.sleep(0.5)
        txt = sess.default_buffer.text
        searching = bool(getattr(sess.app, "is_searching", None) and sess.app.is_searching())
        print(f"Ctrl+R 搜索 'push' → buffer={txt!r}  is_searching={searching}")
        inp.send_bytes(b"\r")
        try:
            await asyncio.wait_for(task, timeout=2)
        except Exception:
            pass
        return "push" in txt


async def main():
    print("=== 修复前 / 后 ===")
    await probe("不打补丁（现状）", False)
    await probe("补丁：buffer.cwt=True", True)
    print("\n=== 补丁后 Ctrl+R 是否仍工作 ===")
    ok = await check_ctrl_r()
    print("结论:", "✅ Ctrl+R 保留且补全恢复" if ok else "❌ Ctrl+R 受影响")


asyncio.run(main())
