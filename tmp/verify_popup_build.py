# -*- coding: utf-8 -*-
"""验证 build_session 已恢复「边打边弹补全列表」，且 Ctrl+R 仍在。"""
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from lib.terminal.repl import build_session


async def popup():
    s = build_session(commands=["git", "grep", "ls"])
    with create_pipe_input() as inp:
        s.app.input = inp
        s.app.output = DummyOutput()
        t = asyncio.ensure_future(s.prompt_async("> "))
        await asyncio.sleep(0.15)
        inp.send_bytes(b"gi")
        await asyncio.sleep(0.7)
        cs = s.default_buffer.complete_state
        n = len(cs.completions) if cs else 0
        print("① 边打边弹：输入 'gi' → 菜单",
              ("有(%d)" % n) if cs else "无",
              "| cwt =", bool(s.default_buffer.complete_while_typing()))
        inp.send_bytes(b"\r")
        try:
            await asyncio.wait_for(t, timeout=2)
        except Exception:
            pass
        return bool(cs)


async def ctrl_r():
    s = build_session(commands=["git", "ls"], history=InMemoryHistory(
        ["git status", "git push origin main", "ls -la"]))
    with create_pipe_input() as inp:
        s.app.input = inp
        s.app.output = DummyOutput()
        t = asyncio.ensure_future(s.prompt_async("> "))
        await asyncio.sleep(0.2)
        inp.send_bytes(b"\x12")          # Ctrl+R
        await asyncio.sleep(0.3)
        inp.send_bytes(b"push")
        await asyncio.sleep(0.5)
        inp.send_bytes(b"\r")            # 接受搜索结果
        await asyncio.sleep(0.3)
        txt = s.default_buffer.text
        print("② Ctrl+R：搜索 'push' → buffer =", repr(txt))
        inp.send_bytes(b"\r")
        try:
            await asyncio.wait_for(t, timeout=2)
        except Exception:
            pass
        return "push" in txt


async def main():
    a = await popup()
    b = await ctrl_r()
    print("\n结论:", "✅ 两者共存" if (a and b) else "❌ 仍有缺失")
    return 0 if (a and b) else 1


sys.exit(asyncio.run(main()))
