# -*- coding: utf-8 -*-
"""验证：TUI 里提交问题不再报「signal only works in main thread」。

做法：PTY 起真实 TUI，把最底层 API 调用 call_ai_api_sse 换成抛标记异常的桩，
提交一句 "hi"，断言：
  1) 屏幕上不再出现 signal only works in main thread；
  2) 出现了桩的标记异常（证明流程确实走到了发请求那一步，而不是被别的问题挡住）；
  3) 侧栏用的是新版双语文案（（暂无任务），不是旧的「（暂无任务 / no tasks）」）。

用法：python3 tmp/tui_signal_check.py
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
def _stub(*a, **k):
    raise RuntimeError("STUBBED-API-CALL")
api.call_ai_api_sse = _stub                 # 只桩掉最底层网络调用
from bin.ai_tui import _build_tui, _enable_alt_enter_keys
_enable_alt_enter_keys()
ctx = {"lang": "chinese", "session_id": str(uuid.uuid4()), "memory_mode": "global",
       "cwd": os.getcwd(), "quiet": False, "show_time": True,
       "session_start": time.time(), "mode": "normal"}
App = _build_tui()
App({"user_home_dir": tempfile.mkdtemp(prefix="onyx_sig_")}, ctx).run()
''' % ROOT

ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][A-Z0-9]|\x1b[=>]|\x1b[<>=]")

FAILS = []


def check(name, cond, extra=""):
    print(f"{'✅' if cond else '❌'} {name}{(' | ' + str(extra)[:90]) if extra else ''}")
    if not cond:
        FAILS.append(name)
    return cond


def main():
    master, slave = __import__("pty").openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 32, 100, 0, 0))
    env = dict(os.environ, TERM="xterm-256color", COLUMNS="100", LINES="32",
               PYTHONPATH=ROOT, PYTHONIOENCODING="utf-8")
    p = subprocess.Popen([sys.executable, "-u", "-c", RUNNER], stdin=slave, stdout=slave,
                         stderr=slave, env=env, close_fds=True, cwd=ROOT)
    os.close(slave)
    buf = b""

    def pump(sec):
        nonlocal buf
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
        screen = pump(6.0)
        check("侧栏用新版双语文案", "（暂无任务）" in screen and "（暂无任务 / no tasks）" not in screen)

        os.write(master, b"hi\r")          # 单行框回车 → 提交
        screen = pump(12.0)

        check("不再出现 signal only works in main thread",
              "signal only works in main thread" not in screen)
        idx = screen.find("STUBBED-API-CALL")
        if idx >= 0:
            print(f"    [dbg] 桩错误上下文：{screen[max(0, idx-120):idx+80]!r}")
        check("流程确实走到发请求（出现桩标记异常）", "STUBBED-API-CALL" in screen)
        check("出错后界面仍存活（底部输入框还在）", "输入消息后回车" in screen)

        os.write(master, b"\x11")          # Ctrl+Q 退出
        pump(1.5)
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
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
