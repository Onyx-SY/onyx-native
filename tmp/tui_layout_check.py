# -*- coding: utf-8 -*-
"""回归：全屏模式下布局尺寸必须与终端一致（防止再次出现画面错位）。

背景：之前用 `os.environ["COLUMNS"] = 日志区宽度` 让 Rich 对齐，结果骗到了
Textual 自己的尺寸判断（shutil.get_terminal_size 会读 COLUMNS）→ 整个界面按错误
宽度排版、文字互相重叠。本测试断言：应用不再改 COLUMNS/LINES，且各控件尺寸正常。

用法：python3 tmp/tui_layout_check.py
"""
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
LOG = os.path.join(ROOT, "tmp", "layout_debug.log")
if os.path.exists(LOG):
    os.remove(LOG)

COLS, ROWS = 110, 40

RUNNER = r'''
import os, sys, tempfile, time, uuid
sys.path.insert(0, %r)
from bin.ai_tui import _build_tui, _enable_alt_enter_keys
_enable_alt_enter_keys()
ctx = {"lang": "chinese", "session_id": str(uuid.uuid4()), "memory_mode": "global",
       "cwd": os.getcwd(), "quiet": False, "show_time": True,
       "session_start": time.time(), "mode": "normal"}
App = _build_tui()
app = App({"user_home_dir": tempfile.mkdtemp(prefix="onyx_layout_")}, ctx)

def _dump(tag):
    try:
        log = app.query_one("#log")
        side = app.query_one("#sidebar")
        with open(%r, "a", encoding="utf-8") as f:
            f.write("%%s app=%%s log=%%s content=%%s sidebar=%%s COLUMNS=%%r LINES=%%r\n" %% (
                tag, app.size, log.size, log.content_size, side.size,
                os.environ.get("COLUMNS"), os.environ.get("LINES")))
    except Exception as e:
        with open(%r, "a", encoding="utf-8") as f:
            f.write("%%s dump error %%r\n" %% (tag, e))

_orig = App.on_mount
def _mount(self):
    _orig(self)
    self.set_timer(1.5, lambda: _dump("mounted"))
App.on_mount = _mount
app.run()
''' % (ROOT, LOG, LOG)

master, slave = pty.openpty()
fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
env = dict(os.environ, TERM="xterm-256color", PYTHONPATH=ROOT, PYTHONIOENCODING="utf-8")
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
                return
            if not data:
                return
            raw += data


try:
    pump(5.0)
    os.write(master, b"\x11")
    pump(1.5)
finally:
    try:
        p.terminate()
        p.wait(timeout=5)
    except Exception:
        p.kill()
    os.close(master)

text = open(LOG, encoding="utf-8").read() if os.path.exists(LOG) else ""
print(text.strip() or "(无输出)")

fails = []
if "dump error" in text or not text.strip():
    fails.append("dump")
if "app=Size(width=110, height=40)" not in text:
    fails.append("app 尺寸与终端不一致")
if "COLUMNS=None" not in text:
    fails.append("应用改写了 COLUMNS（会骗到 Textual 自身尺寸判断）")
if "LINES=None" not in text:
    fails.append("应用改写了 LINES")
import re
m = re.search(r"log=Size\(width=(\d+), height=(\d+)\)", text)
if not m or int(m.group(1)) < 40 or int(m.group(2)) < 10:
    fails.append("日志区尺寸异常（width/height 过小）")

print()
if fails:
    print("❌ 失败：" + " / ".join(fails))
    sys.exit(1)
print("✅ 布局尺寸正常：应用尺寸=终端尺寸，日志区拿到足够宽高，且未改写 COLUMNS/LINES")
