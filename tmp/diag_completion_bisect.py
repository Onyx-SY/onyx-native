# -*- coding: utf-8 -*-
"""二分定位：新输入层里到底是哪个参数让「边打边弹」失效。"""
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
        print(f"{label:34} → 菜单 {'有' if cs else '无'} / 候选 {n}")
        inp.send_bytes(b"\r")
        try:
            await asyncio.wait_for(task, timeout=2)
        except Exception:
            pass
    return bool(cs)


async def main():
    from lib.terminal.repl import complete as C
    from lib.terminal.repl import editor as E
    from lib.terminal.repl import theme

    res = {}
    res["Tiny 基线"] = await probe(
        PromptSession(completer=Tiny(), complete_while_typing=True), "Tiny 基线")
    res["OnyxCompleter"] = await probe(
        PromptSession(completer=C.OnyxCompleter(commands=["git", "grep", "ls"]),
                      complete_while_typing=True), "OnyxCompleter")
    res["Tiny + multiline"] = await probe(
        PromptSession(completer=Tiny(), complete_while_typing=True, multiline=True),
        "Tiny + multiline=True")
    res["Tiny + 新键位"] = await probe(
        PromptSession(completer=Tiny(), complete_while_typing=True,
                      key_bindings=E.build_key_bindings()), "Tiny + 新键位")
    res["Tiny + 新样式"] = await probe(
        PromptSession(completer=Tiny(), complete_while_typing=True,
                      style=theme.build_style()), "Tiny + 新样式")
    res["Tiny + history_search"] = await probe(
        PromptSession(completer=Tiny(), complete_while_typing=True,
                      enable_history_search=True), "Tiny + enable_history_search")
    res["Tiny + suspend"] = await probe(
        PromptSession(completer=Tiny(), complete_while_typing=True,
                      enable_suspend=True), "Tiny + enable_suspend")
    res["Tiny + open_editor"] = await probe(
        PromptSession(completer=Tiny(), complete_while_typing=True,
                      enable_open_in_editor=True), "Tiny + enable_open_in_editor")
    res["Tiny + reserve8"] = await probe(
        PromptSession(completer=Tiny(), complete_while_typing=True,
                      reserve_space_for_menu=8), "Tiny + reserve_space_for_menu=8")
    res["Tiny + toolbar"] = await probe(
        PromptSession(completer=Tiny(), complete_while_typing=True,
                      bottom_toolbar=lambda: "x"), "Tiny + bottom_toolbar")
    res["Tiny + Tiny 再测"] = await probe(
        PromptSession(completer=Tiny(), complete_while_typing=True), "Tiny 复测（排除偶发）")

    print("\n—— 结论 ——")
    bad = [k for k, v in res.items() if not v]
    print("菜单未出现:", bad if bad else "无")
    for k, v in res.items():
        print(f"   {'✅' if v else '❌'} {k}")


asyncio.run(main())
