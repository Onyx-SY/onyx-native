# -*- coding: utf-8 -*-
"""调试：inline 模式下 App / #log 的实际尺寸与内容高度。"""
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
LOG = os.path.join(ROOT, "tmp", "inline_debug.log")
if os.path.exists(LOG):
    os.remove(LOG)

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
app = App({"user_home_dir": tempfile.mkdtemp(prefix="onyx_inline_")}, ctx)

def _dump(tag):
    try:
        log = app.query_one("#log")
        with open(%r, "a", encoding="utf-8") as f:
            f.write("%%s app.size=%%s log.size=%%s log.content=%%s\n" %% (
                tag, app.size, log.size, log.content_size))
    except Exception as e:
        with open(%r, "a", encoding="utf-8") as f:
            f.write("%%s dump error %%r\n" %% (tag, e))

_orig_mount = App.on_mount
def _mount(self):
    _orig_mount(self)
    try:
        self.screen.styles.height = 24
    except Exception:
        pass
    self.set_timer(1.5, lambda: _dump("mounted"))
    self.set_timer(6.0, lambda: _dump("after-submit"))
App.on_mount = _mount
app.run(inline=True, size=(110, 25))
''' % (ROOT, LOG, LOG)

master, slave = pty.openpty()
fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 110, 0, 0))
env = dict(os.environ, TERM="xterm-256color", PYTHONPATH=ROOT, PYTHONIOENCODING="utf-8")
env.pop("COLUMNS", None)
env.pop("LINES", None)
p = subprocess.Popen([sys.executable, "-u", "-c", RUNNER], stdin=slave, stdout=slave,
                     stderr=slave, env=env, close_fds=True, cwd=ROOT)
os.close(slave)


def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        r, _, _ = select.select([master], [], [], 0.1)
        if r:
            try:
                os.read(master, 65536)
            except OSError:
                return


try:
    pump(6.0)
    os.write(master, b"hi\r")
    pump(4.0)
    os.write(master, b"\x11")
    pump(1.5)
finally:
    try:
        p.terminate()
        p.wait(timeout=5)
    except Exception:
        p.kill()
    os.close(master)

print("=== inline_debug.log ===")
print(open(LOG, encoding="utf-8").read() if os.path.exists(LOG) else "(无)")
