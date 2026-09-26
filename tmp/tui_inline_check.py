# -*- coding: utf-8 -*-
"""验证：TUI 走 inline 模式（不接管全屏 / 不用 alt-screen），且输出仍进日志区。

用法：python3 tmp/tui_inline_check.py
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
App({"user_home_dir": tempfile.mkdtemp(prefix="onyx_inline_")}, ctx).run(inline=True)
''' % ROOT

ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][A-Z0-9]|\x1b[<>=]")
FAILS = []


def check(name, cond, extra=""):
    print(f"{'✅' if cond else '❌'} {name}{(' | ' + str(extra)[:100]) if extra else ''}")
    if not cond:
        FAILS.append(name)
    return cond


master, slave = __import__("pty").openpty()
fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 110, 0, 0))
env = dict(os.environ, TERM="xterm-256color", COLUMNS="110", LINES="40",
           PYTHONPATH=ROOT, PYTHONIOENCODING="utf-8")
env.pop("COLUMNS", None)
env.pop("LINES", None)
p = subprocess.Popen([sys.executable, "-u", "-c", RUNNER], stdin=slave, stdout=slave,
                     stderr=slave, env=env, close_fds=True, cwd=ROOT)
os.close(slave)
raw = b""


def pump(sec):
    global raw
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
            raw += data
    return ANSI.sub("", raw.decode("utf-8", "ignore"))


try:
    text = pump(7.0)

    check("未进入 alt-screen（不接管整屏）", b"\x1b[?1049h" not in raw,
          "alt-screen 序列: %s" % (b"\x1b[?1049h" in raw))
    check("界面已渲染（提示行可见）", "Enter=发送" in text)
    check("日志区/侧栏渲染（TODO 标题可见）", "TODO" in text or "FILES" in text)

    # 发一条消息，确认输出仍进日志区（不糊在终端上）
    mark = len(raw)
    os.write(master, b"hi\r")
    text = pump(6.0)
    seg = ANSI.sub("", raw[mark:].decode("utf-8", "ignore"))
    check("提交后输出进入应用区域", "hi" in seg and ("STUBBED" in seg or "请求失败" in seg),
          seg[-160:].replace("\n", " "))

    os.write(master, b"\x11")
    pump(2.0)
    try:
        rc = p.wait(timeout=6)
    except subprocess.TimeoutExpired:
        rc = "TIMEOUT"
    check("Ctrl+Q 正常退出", rc == 0, rc)
    check("退出后未残留 alt-screen 恢复序列", b"\x1b[?1049l" not in raw)
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
if FAILS:
    print(f"❌ {len(FAILS)} 项失败：{FAILS}")
    sys.exit(1)
print("✅ 全部通过")
