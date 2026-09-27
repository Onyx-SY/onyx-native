# -*- coding: utf-8 -*-
"""端到端复现：主 REPL 虚影补全是否能看到【当前会话】新输入的命令。

走真实 universal_input 路径（pipe 输入驱动），断言：
  1. 会话里缓存的 completer.history_buffer 是否与 il._HISTORY_BUFFER 同一个对象；
  2. 输入一条新命令后，get_smart_suggestion 能否补出它。
"""
import io
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output.vt100 import Vt100_Output, Size

from lib.terminal import input_lib as il


def run_once(inp, out, feed_bytes):
    _Real = il.PromptSession

    def _factory(*args, **kwargs):
        run_once.captured = kwargs
        kwargs.setdefault("input", inp)
        kwargs.setdefault("output", Vt100_Output(out, lambda: Size(rows=24, columns=80)))
        return _Real(*args, **kwargs)

    il.PromptSession = _factory
    il._SESSION_CACHE["key"] = None
    il._SESSION_CACHE["session"] = None

    def feed():
        time.sleep(0.2)
        inp.send_text(feed_bytes)
    threading.Thread(target=feed, daemon=True).start()
    try:
        return il.universal_input(prompt_func=lambda: "> ", user_home_dir="", language="chinese")
    finally:
        il.PromptSession = _Real


def main():
    # 模拟启动时从历史文件加载（旧会话命令）
    il._HISTORY_INITIALIZED = True
    il._HISTORY_BUFFER = ["old-session-cmd", "git status"]
    il._HISTORY_BUFFER[:] = ["old-session-cmd", "git status"]
    print("启动时 _HISTORY_BUFFER =", il._HISTORY_BUFFER)

    out = io.StringIO()
    with create_pipe_input() as inp:
        r1 = run_once(inp, out, "echo hello-world\r")
    print("第 1 次输入返回 =", repr(r1))
    print("第 1 次后 _HISTORY_BUFFER =", il._HISTORY_BUFFER)

    comp = run_once.captured.get("completer")
    print("会话 completer.history_buffer =", getattr(comp, "history_buffer", None))
    print("是否同一对象? ", getattr(comp, "history_buffer", None) is il._HISTORY_BUFFER)
    print("get_smart_suggestion('echo') =", repr(comp.get_smart_suggestion("echo")))
    print("get_smart_suggestion('old')  =", repr(comp.get_smart_suggestion("old")))

    with create_pipe_input() as inp2:
        r2 = run_once(inp2, out, "ech")
        # 第二次调用复用缓存会话 → captured 仍是第一次的 kwargs
    print("第 2 次后 _HISTORY_BUFFER =", il._HISTORY_BUFFER)
    comp2 = run_once.captured.get("completer")
    print("第 2 次 captured completer 是同一个? ", comp2 is comp)
    print("第 2 次 captured.history_buffer is _HISTORY_BUFFER? ",
          getattr(comp2, "history_buffer", None) is il._HISTORY_BUFFER)


if __name__ == "__main__":
    main()
