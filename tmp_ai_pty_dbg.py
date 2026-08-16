#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""调试：直接调用 _exec_ai_pty"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.terminal import exe

print("shell:", repr(exe.get_shell()))
buf = []
rc = exe._exec_ai_pty(r"printf '\033[31mred\033[0m\nplain'", buf, None)
print("rc:", rc, "buf:", repr(buf))
