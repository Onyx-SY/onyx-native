#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""决定性验证：真实 universal_input 里，Ctrl+R 能否搜到【上次会话】的历史？
对照组：第 3 次输入用 ↑，应能召回上次会话命令（证明 _HISTORY_BUFFER 正常）。
实验组：第 2 次输入用 Ctrl+R 搜 "zulu"。
"""
import os
import pty
import sys
import time
import fcntl
import select
import struct
import termios

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
PROBE_HOME = os.path.join(HERE, "_probe_home")
RESULT = os.path.join(HERE, "_probe_result.txt")

CHILD = r'''
import sys, os
sys.path.insert(0, ".")
from lib.terminal.input_lib import universal_input
home = os.path.abspath("tmp/_probe_home")
res = []
for i in range(3):
    try:
        r = universal_input(prompt_func=lambda: "P> ", user_home_dir=home,
                            virtual_root="", sys_type="Linux/macOS")
    except Exception:
        import traceback
        r = "<ERR>" + traceback.format_exc()
    res.append(r)
    open("tmp/_probe_result.txt", "w").write(repr(res))
open("tmp/_probe_result.txt", "w").write(repr(res))
'''


def main():
    os.makedirs(PROBE_HOME, exist_ok=True)
    with open(os.path.join(PROBE_HOME, ".onyx_history.txt"), "w", encoding="utf-8") as f:
        f.write("echo zulu_from_prev_run\n")
    if os.path.exists(RESULT):
        os.unlink(RESULT)

    pid, master = pty.fork()
    if pid == 0:
        os.chdir(ROOT)
        os.execv(sys.executable, [sys.executable, "-c", CHILD])

    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    log = []

    def wait_prompt(token="P> ", timeout=25):
        buf = b""
        end = time.time() + timeout
        while time.time() < end:
            r, _, _ = select.select([master], [], [], 0.2)
            if not r:
                continue
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            if token.encode() in buf:
                log.append(buf.decode("utf-8", "replace"))
                return True
        log.append(buf.decode("utf-8", "replace"))
        return False

    def flush(t=1.5):
        end = time.time() + t
        while time.time() < end:
            r, _, _ = select.select([master], [], [], 0.2)
            if not r:
                continue
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            log.append(chunk.decode("utf-8", "replace"))

    ok1 = wait_prompt()
    os.write(master, b"echo typed_in_this_run\r")
    ok2 = wait_prompt()
    os.write(master, b"\x12")
    time.sleep(0.8)
    os.write(master, b"zulu")
    time.sleep(0.8)
    os.write(master, b"\r")
    ok3 = wait_prompt()
    os.write(master, b"\x1b[A")
    time.sleep(0.6)
    os.write(master, b"\r")
    flush(12.0)

    print(f"prompt 就绪: #1={ok1} #2={ok2} #3={ok3}")
    print("── 完整 pty 记录 ──")
    print("".join(log).replace("\x1b", "<ESC>")[-1500:])
    print()
    print("── 结果（3 次 universal_input 返回值）──")
    if os.path.exists(RESULT):
        with open(RESULT, encoding="utf-8") as f:
            print(f.read())
    else:
        print("（无结果文件）")
    try:
        os.kill(pid, 9)
    except ProcessLookupError:
        pass


if __name__ == "__main__":
    main()
