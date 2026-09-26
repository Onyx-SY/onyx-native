# -*- coding: utf-8 -*-
"""探测：能否用 pipe input 无头驱动 MultiLineEditor。"""
import contextvars
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from prompt_toolkit.application import create_app_session  # noqa: E402
from prompt_toolkit.input import create_pipe_input  # noqa: E402
from prompt_toolkit.output import DummyOutput  # noqa: E402

from lib.terminal.mul_line import MultiLineEditor  # noqa: E402

result = {}

with create_pipe_input() as inp:
    with create_app_session(input=inp, output=DummyOutput()):
        ctx = contextvars.copy_context()
        ed = MultiLineEditor()

        def _run():
            result["v"] = ed.edit("abc")

        t = threading.Thread(target=lambda: ctx.run(_run), daemon=True)
        t.start()
        time.sleep(0.6)
        inp.send_text("d")
        time.sleep(0.3)
        inp.send_text("\r")          # Enter → 换行
        time.sleep(0.3)
        inp.send_text("e")
        time.sleep(0.3)
        inp.send_text("\x1b\r")      # Alt+Enter → 提交
        t.join(timeout=6)

print("thread alive:", t.is_alive())
print("result =", repr(result.get("v")))
