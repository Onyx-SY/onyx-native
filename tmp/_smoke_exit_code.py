# -*- coding: utf-8 -*-
"""退出冒烟：ai → /lang en → /lang cn → /quit → exit，检查退出码（跑 N 轮）。"""
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
LANG_FILE = os.path.join(os.path.expanduser("~"), ".config", "onyx", "language")


def one_round(idx):
    buf = bytearray()

    def pump(m, sec):
        end = time.time() + sec
        while time.time() < end:
            r, _, _ = select.select([m], [], [], 0.05)
            if r:
                try:
                    d = os.read(m, 65536)
                except OSError:
                    return
                if not d:
                    return
                buf.extend(d)

    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 100, 0, 0))
    env = dict(os.environ, TERM="xterm-256color", COLUMNS="100", LINES="40",
               PYTHONPATH=ROOT, PYTHONIOENCODING="utf-8", PYTHONFAULTHANDLER="1")
    p = subprocess.Popen([sys.executable, "Main.py"], stdin=slave, stdout=slave,
                         stderr=slave, env=env, close_fds=True, cwd=ROOT,
                         start_new_session=True)
    os.close(slave)
    pump(master, 22)
    os.write(master, b"ai\n")
    pump(master, 9)
    os.write(master, "/lang en\n".encode())
    pump(master, 4)
    os.write(master, "/lang cn\n".encode())
    pump(master, 4)
    os.write(master, "/quit\n".encode())
    pump(master, 6)
    os.write(master, b"exit\n")
    pump(master, 10)
    t0 = time.time()
    while time.time() - t0 < 25:
        if p.poll() is not None:
            break
        pump(master, 0.5)
    rc = p.poll()
    if rc is None:
        p.kill()
        rc = p.poll()
    tail = bytes(buf[-800:]).decode("utf-8", "ignore").replace("\x1b", "^[")
    try:
        os.close(master)
    except OSError:
        pass
    print("round %d rc=%s" % (idx, rc))
    if rc != 0:
        print("  tail:", tail[-600:])
    return rc


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    try:
        orig = open(LANG_FILE, encoding="utf-8").read().strip() or "chinese"
    except Exception:
        orig = "chinese"
    codes = [one_round(i + 1) for i in range(rounds)]
    try:
        with open(LANG_FILE, "w", encoding="utf-8") as f:
            f.write(orig)
    except Exception:
        pass
    print("codes:", codes)
    print("RESULT:", "PASS" if all(c == 0 for c in codes) else "FAIL")
    return 0 if all(c == 0 for c in codes) else 1


if __name__ == "__main__":
    sys.exit(main())
