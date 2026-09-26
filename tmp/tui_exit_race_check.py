# -*- coding: utf-8 -*-
"""健壮性检查：AI 运行中（worker 正在跑）立刻 Ctrl+Q 退出，会不会在退出后
把线程异常/回溯打到已恢复的终端上（污染屏幕）。

用法：python3 tmp/tui_exit_race_check.py
"""
import fcntl
import os
import select
import struct
import subprocess
import sys
import termios
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

RUNNER = r'''
import os, sys, tempfile, time, uuid
sys.path.insert(0, %r)
import bin.ai_lib.api as api
def _slow_stub(*a, **k):
    time.sleep(8)                      # 模拟一次慢请求，留出"运行中退出"的窗口
    raise RuntimeError("SLOW-STUB")
api.call_ai_api_sse = _slow_stub
from bin.ai_tui import _build_tui, _enable_alt_enter_keys
_enable_alt_enter_keys()
ctx = {"lang": "chinese", "session_id": str(uuid.uuid4()), "memory_mode": "global",
       "cwd": os.getcwd(), "quiet": False, "show_time": True,
       "session_start": time.time(), "mode": "normal"}
App = _build_tui()
App({"user_home_dir": tempfile.mkdtemp(prefix="onyx_race_")}, ctx).run()
''' % ROOT

master, slave = __import__("pty").openpty()
fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 32, 100, 0, 0))
env = dict(os.environ, TERM="xterm-256color", COLUMNS="100", LINES="32",
           PYTHONPATH=ROOT, PYTHONIOENCODING="utf-8")
p = subprocess.Popen([sys.executable, "-u", "-c", RUNNER], stdin=slave, stdout=slave,
                     stderr=slave, env=env, close_fds=True, cwd=ROOT)
os.close(slave)
buf = b""


def pump(sec):
    global buf
    end = time.time() + sec
    while time.time() < end:
        r, _, _ = select.select([master], [], [], 0.1)
        if r:
            try:
                data = os.read(master, 65536)
            except OSError:
                break
            if not data:
                break
            buf += data
    return buf.decode("utf-8", "ignore")


try:
    pump(6.0)
    os.write(master, b"hi\r")      # 提交 → worker 进入 8s 慢请求
    pump(1.0)
    os.write(master, b"\x11")      # 立刻 Ctrl+Q 退出
    pump(4.0)
    rc = p.poll()
    out = pump(2.0)
    print("退出码:", rc)
    print("退出后输出里是否出现线程回溯:", ("Traceback" in out) or ("Exception in thread" in out))
    tail = out[-500:]
    print("尾部 500 字符:", repr(tail))
finally:
    try:
        p.terminate()
        p.wait(timeout=5)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass
    try:
        os.close(master)
    except Exception:
        pass
