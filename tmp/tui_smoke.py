# -*- coding: utf-8 -*-
"""TUI 冒烟测试：PTY 启动 → 检查布局元素 → Ctrl+Q 退出。"""
import fcntl
import os
import pty
import select
import struct
import subprocess
import sys
import termios
import time


def run(cols=120, rows=40, total=4.0, key_delay=1.5, keys=b"\x11"):
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    env = dict(os.environ, TERM="xterm-256color", COLUMNS=str(cols), LINES=str(rows), PYTHONPATH=".")
    p = subprocess.Popen([sys.executable, "tmp/tui_run.py"], stdin=slave, stdout=slave,
                         stderr=slave, env=env, close_fds=True)
    os.close(slave)
    buf = b""
    t0 = time.time()
    sent = False
    while time.time() - t0 < total:
        r, _, _ = select.select([master], [], [], 0.2)
        if r:
            try:
                data = os.read(master, 65536)
            except OSError:
                break
            if not data:
                break
            buf += data
        if not sent and time.time() - t0 > key_delay:
            os.write(master, keys)
            sent = True
    try:
        p.wait(timeout=6)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        p.kill()
        rc = "TIMEOUT"
    try:
        os.close(master)
    except Exception:
        pass
    return rc, buf


rc, buf = run()
txt = buf.decode("utf-8", "replace")
print("exit:", rc)
print("bytes:", len(txt))
for kw in ["Onyx AI", "TUI", "TODO", "FILES", "输入消息"]:
    print(f"contains {kw!r}:", kw in txt)
