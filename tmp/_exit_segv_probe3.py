# -*- coding: utf-8 -*-
"""复现：真实一轮 AI 对话（含 RunCommand 工具调用）后退出 TUI → 进程退出是否 SIGSEGV。"""
import fcntl
import os
import pty
import select
import struct
import subprocess
import sys
import termios
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
buf = bytearray()


def _pump(master, sec):
    end = time.time() + sec
    while time.time() < end:
        r, _, _ = select.select([master], [], [], 0.05)
        if r:
            try:
                data = os.read(master, 65536)
            except OSError:
                return
            if not data:
                return
            buf.extend(data)


def run(steps, boot_wait=22.0):
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 100, 0, 0))
    env = dict(os.environ, TERM="xterm-256color", COLUMNS="100", LINES="40",
               PYTHONPATH=ROOT, PYTHONIOENCODING="utf-8", PYTHONFAULTHANDLER="1")
    for _item in (os.environ.get("PROBE_EXTRA_ENV") or "").split(";"):
        if "=" in _item:
            _kk, _vv = _item.split("=", 1)
            env[_kk] = _vv
    p = subprocess.Popen([sys.executable, "Main.py"], stdin=slave, stdout=slave,
                         stderr=slave, env=env, close_fds=True, cwd=ROOT,
                         start_new_session=True)
    os.close(slave)
    _pump(master, boot_wait)
    for label, payload, wait in steps:
        os.write(master, payload if isinstance(payload, bytes) else payload.encode())
        _pump(master, wait)
        print("=== AFTER %s (%.0fs) ===" % (label, wait))
        print(bytes(buf[-800:]).decode("utf-8", "ignore"))
        buf.clear()
    t0 = time.time()
    while time.time() - t0 < 40:
        if p.poll() is not None:
            break
        _pump(master, 0.5)
    rc = p.poll()
    if rc is None:
        p.kill()
        rc = p.poll()
    print("=== EXIT CODE:", rc, "===")
    print("=== FINAL TAIL ===\n", bytes(buf[-5000:]).decode("utf-8", "ignore"))
    try:
        os.close(master)
    except OSError:
        pass
    return rc


if __name__ == "__main__":
    run([
        ("ai", "ai\n", 8.0),
        ("chat_tool", "用 RunCommand 跑一下 echo hi，然后一句话告诉我结果\n", 70.0),
        ("quit", "/quit\n", 6.0),
        ("exit", "exit\n", 15.0),
    ])
