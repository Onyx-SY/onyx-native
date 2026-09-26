# -*- coding: utf-8 -*-
"""验证健壮性缺陷：TUI 里执行 /key（或 /model）会卡住 —— 工作线程里调用了裸 input()。

原理：_run_one 在工作线程里跑 _dispatch_slash("/key")，而该分支用 input() 读 stdin；
Textual 正把 stdin 设为 raw 模式并由自己的输入线程读取 → 两边抢 fd0：
  - 阻塞在 input() 上，worker 线程不返回，_busy 永远为 True（后续输入只会排队）；
  - 用户敲的键可能被 input() 吃掉，界面看起来"没反应"。

用法：python3 tmp/tui_slash_hang_check.py
"""
import fcntl
import os
import re
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
api.call_ai_api_sse = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("STUBBED"))
from bin.ai_tui import _build_tui, _enable_alt_enter_keys
_enable_alt_enter_keys()
ctx = {"lang": "chinese", "session_id": str(uuid.uuid4()), "memory_mode": "global",
       "cwd": os.getcwd(), "quiet": False, "show_time": True,
       "session_start": time.time(), "mode": "normal"}
App = _build_tui()
App({"user_home_dir": os.path.expanduser("~")}, ctx).run()
''' % ROOT

ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][A-Z0-9]|\x1b[=>]|\x1b[<>=]")

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
                return ANSI.sub("", buf.decode("utf-8", "ignore"))
            if not data:
                return ANSI.sub("", buf.decode("utf-8", "ignore"))
            buf += data
    return ANSI.sub("", buf.decode("utf-8", "ignore"))


try:
    pump(6.0)
    print("① 启动完成")

    # ① /key：会走到裸 input()
    os.write(master, b"/model\r")
    s = pump(4.0)
    print("② 发 /model 后屏幕是否出现 Key 信息:", "Key:" in s or "密钥" in s)

    # ② 再发一条普通消息：如果 worker 卡在 input()，它会被吃掉/只进队列
    before = len(s)
    os.write(master, b"hello-world-probe\r")
    s = pump(6.0)
    print("③ 普通消息是否被处理（屏幕出现 › hello-world-probe）:", "hello-world-probe" in s[before:])
    print("③b 桩错误是否出现（说明 worker 有在干活）:", "STUBBED" in s[before:])

    # ③ 是否还能退出
    os.write(master, b"\x11")
    pump(3.0)
    alive = p.poll() is None
    print("④ Ctrl+Q 后进程仍在运行（=卡死）:", alive)
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
