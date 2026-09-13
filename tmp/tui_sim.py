#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TUI reproduction harness.

Runs a TUI program two ways on a real PTY host terminal:
  A) directly (baseline)
  B) through Onyx's persistent-shell passthrough
Captures the byte stream each path would deliver to the host terminal and
renders it with a small VT100 emulator so the two screens can be compared.

Usage: python3 tmp/tui_sim.py [prog] [rows] [cols]
"""
import os
import sys
import pty
import fcntl
import termios
import struct
import select
import time
import re

ROOT = os.getcwd()
sys.path.insert(0, ROOT)

PROG = sys.argv[1] if len(sys.argv) > 1 else 'nano'
ROWS = int(sys.argv[2]) if len(sys.argv) > 2 else 61
COLS = int(sys.argv[3]) if len(sys.argv) > 3 else 66


# ------------------------------------------------------------------ VT screen
class Screen:
    def __init__(self, rows, cols):
        self.rows, self.cols = rows, cols
        self.buf = [[' '] * cols for _ in range(rows)]
        self.r = self.c = 0
        self.alt = None
        self.events = []

    def _clamp(self):
        self.r = max(0, min(self.rows - 1, self.r))
        self.c = max(0, min(self.cols - 1, self.c))

    def put(self, ch):
        if 0 <= self.r < self.rows and 0 <= self.c < self.cols:
            self.buf[self.r][self.c] = ch
        self.c += 1
        if self.c >= self.cols:
            self.c = self.cols - 1

    def clear(self):
        self.buf = [[' '] * self.cols for _ in range(self.rows)]

    def feed(self, data):
        s = data.decode('utf-8', errors='replace')
        i, n = 0, len(s)
        while i < n:
            ch = s[i]
            if ch == '\x1b':
                m = re.match(r'\x1b\[(\??)([0-9;]*)([A-Za-z])', s[i:])
                if m:
                    priv, params, cmd = m.group(1), m.group(2), m.group(3)
                    nums = [int(x) for x in params.split(';') if x.isdigit()]
                    i += m.end()
                    self._csi(priv, nums, cmd)
                    continue
                m = re.match(r'\x1b[()][0-9A-B]', s[i:])
                if m:
                    i += m.end()
                    continue
                m = re.match(r'\x1b[=>]', s[i:])
                if m:
                    i += m.end()
                    continue
                i += 1
                continue
            if ch == '\r':
                self.c = 0
            elif ch == '\n':
                self.r += 1
                if self.r >= self.rows:
                    self.r = self.rows - 1
                    self.buf.pop(0)
                    self.buf.append([' '] * self.cols)
                    self.events.append('scroll')
            elif ch == '\b':
                self.c = max(0, self.c - 1)
            elif ch == '\t':
                self.c = min(self.cols - 1, self.c + (8 - self.c % 8))
            elif ch >= ' ':
                self.put(ch)
            i += 1

    def _csi(self, priv, nums, cmd):
        if cmd in 'Hf':
            self.r = (nums[0] - 1) if len(nums) > 0 else 0
            self.c = (nums[1] - 1) if len(nums) > 1 else 0
            self._clamp()
        elif cmd == 'A':
            self.r -= (nums[0] if nums else 1); self._clamp()
        elif cmd == 'B':
            self.r += (nums[0] if nums else 1); self._clamp()
        elif cmd == 'C':
            self.c += (nums[0] if nums else 1); self._clamp()
        elif cmd == 'D':
            self.c -= (nums[0] if nums else 1); self._clamp()
        elif cmd == 'G':
            self.c = (nums[0] if nums else 1) - 1; self._clamp()
        elif cmd == 'd':
            self.r = (nums[0] if nums else 1) - 1; self._clamp()
        elif cmd == 'J':
            mode = nums[0] if nums else 0
            self.events.append('ED%d' % mode)
            if mode == 2:
                self.clear()
            elif mode == 0:
                for cc in range(self.c, self.cols):
                    self.buf[self.r][cc] = ' '
                for rr in range(self.r + 1, self.rows):
                    self.buf[rr] = [' '] * self.cols
        elif cmd == 'K':
            mode = nums[0] if nums else 0
            if mode == 0:
                for cc in range(self.c, self.cols):
                    self.buf[self.r][cc] = ' '
            elif mode == 2:
                self.buf[self.r] = [' '] * self.cols
        elif cmd == 'r':
            self.events.append('DECSTBM%s' % (nums,))
        elif cmd == 'h' and priv == '?':
            if 1049 in nums or 47 in nums or 1047 in nums:
                self.alt = [row[:] for row in self.buf]
                self.clear()
                self.r = self.c = 0
                self.events.append('ALTON')
        elif cmd == 'l' and priv == '?':
            if 1049 in nums or 47 in nums or 1047 in nums:
                if self.alt is not None:
                    self.buf = self.alt
                    self.alt = None
                self.r = self.c = 0
                self.events.append('ALTOFF')

    def dump(self):
        lines = [''.join(row).rstrip() for row in self.buf]
        while lines and not lines[-1]:
            lines.pop()
        return '\n'.join(lines)


# ------------------------------------------------------------------ driver
def drive(child_fn, tag, keys, timeout=30):
    """keys: list of (delay_after_ui_seconds, bytes)"""
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', ROWS, COLS, 0, 0))
    pid = os.fork()
    if pid == 0:
        try:
            os.close(master)
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
            os.dup2(slave, 0)
            os.dup2(slave, 1)
            os.dup2(slave, 2)
            if slave > 2:
                os.close(slave)
            child_fn()
        except BaseException:
            import traceback
            traceback.print_exc()
        finally:
            os._exit(0)
    os.close(slave)
    chunks = []
    ui_seen = None
    sent = 0
    t0 = time.time()
    while time.time() - t0 < timeout:
        r, _, _ = select.select([master], [], [], 0.05)
        if r:
            try:
                d = os.read(master, 65536)
            except OSError:
                break
            if not d:
                break
            chunks.append(d)
            if ui_seen is None:
                ui_seen = time.time()
        if sent < len(keys):
            if time.time() - t0 >= keys[sent][0]:
                try:
                    os.write(master, keys[sent][1])
                except OSError:
                    pass
                sent += 1
        if sent >= len(keys) and ui_seen is not None:
            p, _st = os.waitpid(pid, os.WNOHANG)
            if p:
                time.sleep(0.4)
                while True:
                    r, _, _ = select.select([master], [], [], 0.2)
                    if not r:
                        break
                    try:
                        d = os.read(master, 65536)
                    except OSError:
                        break
                    if not d:
                        break
                    chunks.append(d)
                break
    try:
        os.close(master)
    except OSError:
        pass
    return b''.join(chunks)


def report(tag, data, extra=''):
    print('=' * 72)
    print('### %s   bytes=%d  alt-on=%d alt-off=%d ED2(clear)=%d' % (
        tag, len(data), data.count(b'\x1b[?1049h'), data.count(b'\x1b[?1049l'),
        data.count(b'\x1b[2J')))
    print('   scroll-regions : %s' % sorted(set(re.findall(rb'\x1b\[[0-9;]*r', data)))[:6])
    print('   max col move   : %s' % sorted(set(int(x) for x in re.findall(rb'\x1b\[(\d+)G', data)), reverse=True)[:5])
    sc = Screen(ROWS, COLS)
    sc.feed(data)
    # 取"TUI 全屏期间"的屏幕快照：在最后一次退出 alt-screen 之前的内容
    idx = data.rfind(b'\x1b[?1049l')
    snap = Screen(ROWS, COLS)
    snap.feed(data[:idx] if idx > 0 else data)
    print('   events         : %s' % (sc.events[:20],))
    print('--- screen DURING TUI (%dx%d) %s---' % (ROWS, COLS, extra))
    print(snap.dump())
    print('--- screen AFTER exit ---')
    print(sc.dump())
    print()
    return sc, snap
