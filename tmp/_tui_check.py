# -*- coding: utf-8 -*-
"""TUI 修复验证：末尾无多余换行 + 控制码序列不被扣住。"""
import sys
import os
import time

sys.path.insert(0, '.')
from lib.terminal import exe   # noqa: E402


class FB:
    def __init__(self):
        self.data = bytearray()

    def write(self, b):
        self.data.extend(b)

    def flush(self):
        pass


class FS:
    def __init__(self):
        self.buffer = FB()


class SS:
    def __init__(self, fd):
        self._fd = fd

    def fileno(self):
        return self._fd


r, w = os.pipe()
sys.__stdout__ = FS()
sys.stdin = SS(r)
exe.DEBUG_ENABLED = False

s = exe.PersistentShell(cwd=os.getcwd())

cases = [
    ("cd-noout",     "cd ."),
    ("true-noout",   "true"),
    ("plain",        "printf A"),
    ("with-newline", "printf 'A\\n'"),
    ("tui-esc",      "printf '\\033[2J\\033[HXY'"),
    ("tui-esc-nl",   "printf '\\033[2J\\033[HXY\\n'"),
    ("ls",           "ls -d ."),
]
for name, cmd in cases:
    t = time.time()
    buf = []
    rc, _ = s.execute(cmd, buf)
    dt = time.time() - t
    out = ''.join(buf)
    print('%-14s rc=%-5s dt=%.2fs  tail=%r' % (name, rc, dt, out[-36:]))

s.cleanup()
