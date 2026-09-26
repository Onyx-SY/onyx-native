# -*- coding: utf-8 -*-
"""step-4 验证：修复后 TUI 里 /key、/model 不再卡死（会弹模态框，ESC 取消后 worker 仍存活）。

- user_home_dir 用「真实 ~/.config/onyx 的副本」，保证 /key /model 能走到输入步骤，
  同时所有写入都落在临时目录，绝不碰真实配置。
- 每个命令后先发 ESC 关闭模态框，再发探针消息；探针被处理 = worker 仍活着。

用法：python3 tmp/tui_slash_sweep.py
"""
import fcntl
import os
import re
import select
import shutil
import struct
import subprocess
import sys
import termios
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

RUNNER = r'''
import os, sys, shutil, tempfile, time, uuid
sys.path.insert(0, %r)
home = tempfile.mkdtemp(prefix="onyx_sweep_")
src = os.path.join(os.path.expanduser("~"), ".config", "onyx")
if os.path.isdir(src):
    shutil.copytree(src, os.path.join(home, ".config", "onyx"))   # 只读副本，写不碰真实配置
import bin.ai_lib.api as api
def _stub(*a, **k):
    raise RuntimeError("STUBBED-API")
api.call_ai_api_sse = _stub
from bin.ai_tui import _build_tui, _enable_alt_enter_keys
_enable_alt_enter_keys()
ctx = {"lang": "chinese", "session_id": str(uuid.uuid4()), "memory_mode": "global",
       "cwd": os.getcwd(), "quiet": False, "show_time": True,
       "session_start": time.time(), "mode": "normal"}
App = _build_tui()
App({"user_home_dir": home}, ctx).run()
''' % ROOT

ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][A-Z0-9]|\x1b[=>]|\x1b[<>=]")
CMDS = ["/key", "/model", "/help", "/cost", "/tokens", "/doctor", "/memory", "/clear", "/cd /tmp"]

master, slave = __import__("pty").openpty()
fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
env = dict(os.environ, TERM="xterm-256color", COLUMNS="120", LINES="40",
           PYTHONPATH=ROOT, PYTHONIOENCODING="utf-8")
p = subprocess.Popen([sys.executable, "-u", "-c", RUNNER], stdin=slave, stdout=slave,
                     stderr=slave, env=env, close_fds=True, cwd=ROOT)
os.close(slave)
buf = b""
fails = []


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
    return ANSI.sub("", buf.decode("utf-8", "ignore"))


try:
    pump(6.0)
    print("启动 OK（home 为 ~/.config/onyx 的只读副本）\n")
    for i, cmd in enumerate(CMDS):
        mark = len(buf)
        os.write(master, (cmd + "\r").encode())
        pump(2.0)
        cmd_out = ANSI.sub("", buf[mark:].decode("utf-8", "ignore"))
        modal = ("modal" in cmd_out) or ("╭" in cmd_out and "╰" in cmd_out)
        os.write(master, b"\x1b")           # ESC：取消可能弹出的模态框
        pump(1.2)
        os.write(master, ("PROBE-%d\r" % i).encode())
        pump(4.0)
        text = ANSI.sub("", buf.decode("utf-8", "ignore"))
        alive = ("PROBE-%d" % i) in text
        print(f"{'✅' if alive else '❌'} {cmd:<9} worker存活={alive} 命令有输出={len(cmd_out)>0}")
        if not alive:
            fails.append(cmd)
            print("   ↳ worker 卡住，后续命令不再继续")
            break
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

print()
print("❌ 卡死命令：" + ", ".join(fails) if fails else "✅ 全部命令后 worker 均存活")
sys.exit(1 if fails else 0)
