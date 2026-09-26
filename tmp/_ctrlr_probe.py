#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线验证：项目主 REPL 里 Ctrl+R 反向增量搜索能否搜到历史。
用 forkpty 起真实终端子进程（PromptSession 与 input_lib 一样不传 history=），
按提示符同步喂键：echo alpha / echo beta / Ctrl+R -> 'alp' -> Enter。
"""
import os
import pty
import sys
import time
import fcntl
import select
import struct
import termios

CHILD = r'''
import sys
sys.path.insert(0, ".")
from prompt_toolkit import PromptSession
from lib.terminal.kb import create_key_bindings
kb = create_key_bindings(sys_type="Linux/macOS", terminal_type="bash", ptk_config=None)
s = PromptSession(key_bindings=kb)
out = []
for i in range(3):
    try:
        r = s.prompt("P%d> " % i)
    except Exception as e:
        r = "<ERR %s>" % e
    out.append(r)
open("tmp/_ctrlr_result.txt", "w").write(repr(out))
'''

HERE = os.path.dirname(os.path.abspath(__file__))
RESULT = os.path.join(HERE, "_ctrlr_result.txt")


def main():
    if os.path.exists(RESULT):
        os.unlink(RESULT)

    pid, master = pty.fork()
    if pid == 0:
        os.chdir(os.path.join(HERE, ".."))
        os.execv(sys.executable, [sys.executable, "-c", CHILD])

    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    log = []

    def read_until(token, timeout=25):
        buf = b""
        end = time.time() + timeout
        while time.time() < end:
            r, _, _ = select.select([master], [], [], 0.2)
            if r:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
        log.append(buf.decode("utf-8", "replace"))
        return token.encode() in buf

    ok0 = read_until("P0> ")
    os.write(master, b"echo alpha\r")
    ok1 = read_until("P1> ")
    os.write(master, b"echo beta\r")
    ok2 = read_until("P2> ")
    os.write(master, b"\x12")      # Ctrl+R
    time.sleep(0.8)
    os.write(master, b"alp")
    time.sleep(0.8)
    os.write(master, b"\r")        # Enter 接受
    time.sleep(1.2)
    try:
        while True:
            r, _, _ = select.select([master], [], [], 0.3)
            if not r:
                break
            chunk = os.read(master, 65536)
            if not chunk:
                break
            log.append(chunk.decode("utf-8", "replace"))
    except OSError:
        pass

    print(f"prompt 就绪: P0={ok0} P1={ok1} P2={ok2}")
    print("── pty 尾部输出 ──")
    print("".join(log)[-500:])
    print("── 结果 ──")
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
