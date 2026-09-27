# -*- coding: utf-8 -*-
"""验证：历史达到上限后 add_to_history 的重绑定是否会切断 completer 的引用。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib.terminal import input_lib as il
from lib.terminal.com import SmartCompleter

il._HISTORY_INITIALIZED = True
il._HISTORY_BUFFER[:] = [f"oldcmd-{i}" for i in range(il._HISTORY_MAX_MEMORY)]
print("初始长度 =", len(il._HISTORY_BUFFER))

c = SmartCompleter(["ls"], history_buffer=il._HISTORY_BUFFER, user_home_dir="")
print("同一对象? ", c.history_buffer is il._HISTORY_BUFFER)

il.add_to_history("fresh-1")
print("第 1 条后：同一对象?", c.history_buffer is il._HISTORY_BUFFER,
      "| completer 长度 =", len(c.history_buffer), "| buffer 长度 =", len(il._HISTORY_BUFFER))

il.add_to_history("fresh-2")
print("第 2 条后：同一对象?", c.history_buffer is il._HISTORY_BUFFER,
      "| completer 有 fresh-2 ?", "fresh-2" in c.history_buffer,
      "| buffer 有 fresh-2 ?", "fresh-2" in il._HISTORY_BUFFER)
print("get_smart_suggestion('fresh') =", repr(c.get_smart_suggestion("fresh")))
