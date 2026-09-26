# -*- coding: utf-8 -*-
"""step-4/5/7 验证（真实终端）：TUI 下拉补全菜单 + Tab 补全 + Ctrl+C 不再退出。

判定用「新增输出片段」（避免历史帧干扰）：
  1) 输入 / → 菜单出现（片段里能看到多个命令名）
  2) Tab 补全后输入 "X" → 片段里出现 "/helpX"（证明 Tab 把 / 补成了 /help，且焦点没被切走）
  3) 有内容时按 Ctrl+C → 输入框清空（片段里重新出现占位符），且进程仍存活
  4) Ctrl+Q 正常退出（退出码 0）

用法：python3 tmp/tui_menu_pty_check.py
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
App({"user_home_dir": tempfile.mkdtemp(prefix="onyx_menu_pty_")}, ctx).run()
''' % ROOT

ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][A-Z0-9]|\x1b[=>]|\x1b[<>=]")
FAILS = []


def check(name, cond, extra=""):
    print(f"{'✅' if cond else '❌'} {name}{(' | ' + str(extra)[:90]) if extra else ''}")
    if not cond:
        FAILS.append(name)
    return cond


master, slave = __import__("pty").openpty()
fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 34, 110, 0, 0))
env = dict(os.environ, TERM="xterm-256color", COLUMNS="110", LINES="34",
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
    return ANSI.sub("", buf.decode("utf-8", "ignore"))


def since(mark):
    return ANSI.sub("", buf[mark:].decode("utf-8", "ignore"))


try:
    pump(6.0)

    # ── 1. 输入 / → 菜单 ──
    m = len(buf)
    os.write(master, b"/")
    seg = pump(1.5)
    seg_new = since(m)
    check("输入 / 弹出命令菜单（片段含多个命令名）",
          ("/help" in seg_new and "/clear" in seg_new) or ("/help" in seg_new and "/cost" in seg_new),
          seg_new[-160:].replace("\n", " "))

    # ── 2. Tab 补全 + 追加字符 ──
    os.write(master, b"\t")
    pump(1.0)
    m = len(buf)
    os.write(master, b"X")
    seg_new = pump(1.2) and since(m)
    check("Tab 把 / 补成 /help（追加 X 后出现 /helpX）", "/helpX" in seg_new,
          seg_new[-160:].replace("\n", " "))

    # ── 3. Ctrl+C：清空输入但不退出 ──
    m = len(buf)
    os.write(master, b"\x03")
    seg_new = pump(1.5) and since(m)
    check("Ctrl+C 清空输入框（占位符重新出现）", "输入消息后回车" in seg_new,
          seg_new[-160:].replace("\n", " "))
    check("Ctrl+C 之后进程仍存活（没退出 TUI）", p.poll() is None)

    # ── 4. Ctrl+Q 退出 ──
    os.write(master, b"\x11")
    pump(2.0)
    try:
        rc = p.wait(timeout=6)
    except subprocess.TimeoutExpired:
        rc = "TIMEOUT"
    check("Ctrl+Q 正常退出", rc == 0, rc)
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
