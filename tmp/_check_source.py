# -*- coding: utf-8 -*-
"""端到端验证 source 在两条执行路径上的行为。"""
import sys
import os

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

p = os.path.abspath('tmp/_src_test.sh')
with open(p, 'w') as f:
    f.write('export SRC_VAR=from_source\n')

print('--- 1) AI 模式（subprocess）---')
buf = []
rc = exe._exec_ai_subprocess('source %s && echo AI_VAR=$SRC_VAR' % p, buf, None)
print('rc=%s  out=%r' % (rc, ''.join(buf).strip()))

print('--- 2) PTY 持久 shell ---')
shell = exe.PersistentShell(cwd=os.getcwd())
buf = []
rc, _ = shell.execute('source %s && echo PTY_VAR=$SRC_VAR' % p, buf)
print('rc=%s  out=%r' % (rc, ''.join(buf).strip()))

print('--- 3) source 语义：变量是否留在持久 shell ---')
buf = []
rc, _ = shell.execute('echo LATER=$SRC_VAR', buf)
print('rc=%s  out=%r' % (rc, ''.join(buf).strip()))

shell.cleanup()
