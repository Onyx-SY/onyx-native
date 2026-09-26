# -*- coding: utf-8 -*-
"""step-7 验证（真实终端）：PTY 里跑 TUI，验证 Alt+Enter（ESC+CR）多行 + 折叠省略 + 发送。

用法：python3 tmp/tui_pty_check.py
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
import bin.ai_interactive as ai
ai._call_ai_engine = lambda *a, **k: None          # 不真的调 AI
from bin.ai_tui import _build_tui, _enable_alt_enter_keys
_enable_alt_enter_keys()                           # 与 ai_tui_session 一致：补 Alt+Enter
ctx = {"lang": "chinese", "session_id": str(uuid.uuid4()), "memory_mode": "global",
       "cwd": os.getcwd(), "quiet": False, "show_time": True,
       "session_start": time.time(), "mode": "normal"}
App = _build_tui()
App({"user_home_dir": tempfile.mkdtemp(prefix="onyx_pty_")}, ctx).run()
''' % ROOT

ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][A-Z0-9]|\x1b[=>]|\x1b[<>=]")


class Pty:
    def __init__(self, cols=100, rows=30):
        self.master, slave = __import__("pty").openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        env = dict(os.environ, TERM="xterm-256color", COLUMNS=str(cols), LINES=str(rows),
                   PYTHONPATH=ROOT, PYTHONIOENCODING="utf-8")
        self.p = subprocess.Popen([sys.executable, "-u", "-c", RUNNER], stdin=slave,
                                  stdout=slave, stderr=slave, env=env, close_fds=True,
                                  cwd=ROOT)
        os.close(slave)
        self.buf = b""

    def pump(self, seconds=0.6):
        end = time.time() + seconds
        while time.time() < end:
            r, _, _ = select.select([self.master], [], [], 0.1)
            if r:
                try:
                    data = os.read(self.master, 65536)
                except OSError:
                    break
                if not data:
                    break
                self.buf += data
        return self.text()

    def send(self, data: bytes):
        os.write(self.master, data)

    def text(self):
        return ANSI.sub("", self.buf.decode("utf-8", "ignore"))

    def close(self):
        try:
            self.p.terminate()
            self.p.wait(timeout=5)
        except Exception:
            try:
                self.p.kill()
            except Exception:
                pass
        try:
            os.close(self.master)
        except Exception:
            pass


FAILS = []


def check(name, cond, extra=""):
    print(f"{'✅' if cond else '❌'} {name}{(' | ' + str(extra)[:80]) if extra else ''}")
    if not cond:
        FAILS.append(name)
    return cond


def main():
    t = Pty(cols=100, rows=32)
    try:
        screen = t.pump(6.0)
        check("TUI 已启动（出现欢迎语/提示行）",
              "TUI 模式" in screen or "Enter=发送" in screen, screen[-200:])

        # ── Alt+Enter（真实终端发送 ESC+CR）→ 多行模式 ──
        t.send("第一行".encode())
        t.pump(0.6)
        t.send(b"\x1b\r")
        screen = t.pump(1.2)
        check("Alt+Enter（ESC+CR）进入多行模式（提示行出现 Enter=换行）",
              "Enter=换行" in screen, screen[-200:])

        # ── 再敲 5 行（Enter=换行）→ 共 6 行 → 折叠 ──
        for i in range(2, 7):
            t.send(f"L{i}\r".encode())
            t.pump(0.25)
        screen = t.pump(1.0)
        print(f"    [dbg] 文本流里是否出现：'第一行'={'第一行' in screen} "
              f"'L2'={'L2' in screen} 'L3'={'L3' in screen} '省略'={'省略' in screen}")
        print(f"    [dbg] 尾部 400 字符：{screen[-400:]!r}")
        check("6 行触发折叠：出现省略提示", "省略" in screen, screen[-300:])
        check("省略行提示省略行数", "省略 2 行" in screen or "省略 1 行" in screen, "")

        # ── Alt+Enter 发送 ──
        t.send(b"\x1b\r")
        screen = t.pump(1.5)
        check("Alt+Enter 发送后回到单行模式（提示行变回）",
              "Tab=补全" in screen, screen[-200:])

        # ── Ctrl+Q 退出 ──
        t.send(b"\x11")
        try:
            rc = t.p.wait(timeout=6)
        except subprocess.TimeoutExpired:
            rc = "TIMEOUT"
        check("Ctrl+Q 正常退出", rc == 0, rc)
    finally:
        t.close()

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 项失败：{FAILS}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
