# -*- coding: utf-8 -*-
"""step-4 验证（深路径）：TUI 里 /key → 答 y → 进入密钥向导（原本裸 input() 的重灾区）。

期望：向导的平台选择/参数输入全部走模态框，ESC 逐级取消后 worker 仍然存活。

用法：python3 tmp/tui_key_wizard_check.py
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
import os, sys, shutil, tempfile, time, uuid
sys.path.insert(0, %r)
home = tempfile.mkdtemp(prefix="onyx_wiz_")
src = os.path.join(os.path.expanduser("~"), ".config", "onyx")
if os.path.isdir(src):
    shutil.copytree(src, os.path.join(home, ".config", "onyx"))   # 只读副本，写不碰真实配置
import bin.ai_lib.api as api
api.call_ai_api_sse = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("STUBBED"))
from bin.ai_tui import _build_tui, _enable_alt_enter_keys
_enable_alt_enter_keys()
ctx = {"lang": "chinese", "session_id": str(uuid.uuid4()), "memory_mode": "global",
       "cwd": os.getcwd(), "quiet": False, "show_time": True,
       "session_start": time.time(), "mode": "normal"}
App = _build_tui()
App({"user_home_dir": home}, ctx).run()
''' % ROOT

ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][A-Z0-9]|\x1b[=>]|\x1b[<>=]")

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


def check(name, cond, extra=""):
    print(f"{'✅' if cond else '❌'} {name}{(' | ' + str(extra)[:80]) if extra else ''}")
    if not cond:
        fails.append(name)


try:
    pump(6.0)
    os.write(master, b"/key\r")
    s = pump(2.0)
    check("/key 弹出确认模态框", "modal" in s or "╭" in s[-4000:], s[-120:].replace("\n", " "))

    os.write(master, b"y")          # 答 y → 进入密钥向导
    s = pump(2.5)
    check("向导进入平台选择模态框（不是裸 input() 卡死）", "╭" in s[-4000:])

    os.write(master, b"\x1b")       # ESC 取消平台选择
    pump(1.5)
    os.write(master, b"\x1b")       # 再 ESC 兜底
    pump(1.0)

    os.write(master, b"WIZARD-PROBE\r")
    s = pump(4.0)
    check("向导取消后 worker 仍存活", "WIZARD-PROBE" in s)
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
print("❌ 失败：" + ", ".join(fails) if fails else "✅ 全部通过")
sys.exit(1 if fails else 0)
