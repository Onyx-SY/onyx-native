# -*- coding: utf-8 -*-
"""诊断 4：import 引擎后，gc 能否收齐所有模块级 Console（决定修复方案）。"""
import gc
import os
import queue
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bin.ai_tui import _QueueStream          # noqa: E402

q = queue.Queue()
out = _QueueStream(q)
sys.stdout = out
sys.stderr = out

from bin.ai_interactive import _call_ai_engine   # noqa: E402,F401

from rich.console import Console as _C           # noqa: E402

found = [o for o in gc.get_objects() if isinstance(o, _C)]
print("gc Console 数:", len(found), file=sys.__stdout__)

mods = ["bin.ai_cmd", "bin.ai_lib.api", "bin.ai_lib.config", "bin.ai_lib.helpers",
        "bin.ai_lib.mcp_client_core", "bin.ai_lib.mcp_exec", "bin.ai_lib.tool_executors",
        "bin.ai_lib.ui", "bin.ai_interactive", "bin.manage"]
for m in mods:
    mod = sys.modules.get(m)
    if mod is None:
        print(f"  {m}: 未加载", file=sys.__stdout__)
        continue
    con = getattr(mod, "console", None)
    if con is None:
        print(f"  {m}: 无 console 属性", file=sys.__stdout__)
        continue
    print(f"  {m}: 在 gc 集合中 = {con in found}", file=sys.__stdout__)

# 关键模块的 console 现状
import bin.ai_cmd as ac
print("ai_cmd.console.is_terminal =", ac.console.is_terminal, file=sys.__stdout__)
print("ai_cmd.console.color_system =", ac.console.color_system, file=sys.__stdout__)

sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__
