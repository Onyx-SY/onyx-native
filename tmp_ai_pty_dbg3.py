#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""调试：stdin 转发（弱交互）+ Ctrl+C 进程组清理"""
import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.terminal import exe

# ── 1) fd 0 转发：命令 read 一行 → 返回结果 ──
r_fd, w_fd = os.pipe()
_saved_stdin = os.dup(0)
os.dup2(r_fd, 0)
buf = []
rc_box = {}

def _feeder():
    time.sleep(0.5)
    try:
        os.write(w_fd, b"hello-from-user\n")
    finally:
        os.close(w_fd)

t = threading.Thread(target=_feeder, daemon=True)
t.start()
try:
    rc = exe._exec_ai_pty("read line && echo got:$line", buf, None)
finally:
    os.dup2(_saved_stdin, 0)
    os.close(_saved_stdin)
t.join()
print("stdin-forward rc:", rc, "buf:", repr(buf))
assert rc == 0 and buf == ["got:hello-from-user\n"], (rc, buf)

# ── 2) Ctrl+C 清理：killpg SIGINT → 命令退出 ──
p = subprocess.Popen(["sleep", "30"], start_new_session=True)
exe._kill_ai_pty_proc(p)
time.sleep(0.2)
print("kill rc:", p.poll())
assert p.poll() is not None and p.poll() == -2, p.poll()  # -SIGINT

# ── 3) 顽固进程：SIGINT 无效 → SIGKILL 兜底 ──
p2 = subprocess.Popen(
    ["bash", "-c", "trap '' INT; sleep 30"], start_new_session=True)
exe._kill_ai_pty_proc(p2)
time.sleep(0.2)
print("stubborn kill rc:", p2.poll())
assert p2.poll() is not None, p2.poll()

print("ALL OK")
