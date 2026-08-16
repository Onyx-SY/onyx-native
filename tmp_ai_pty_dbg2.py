#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""调试：一次性 PTY 原始字节 + 子进程是否直写 fd1"""
import os
import pty
import select
import subprocess
import sys
import time

# 1) Termux bash -c 在 PTY 里到底输出什么
master_fd, slave_fd = pty.openpty()
p = subprocess.Popen(["bash", "-c", r"printf '\033[31mred\033[0m\nplain'"],
                     stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
                     start_new_session=True)
os.close(slave_fd)
chunks = []
while True:
    r, _, _ = select.select([master_fd], [], [], 2.0)
    if not r:
        break
    try:
        d = os.read(master_fd, 65536)
    except OSError:
        break
    if not d:
        break
    chunks.append(d)
    if p.poll() is not None:
        time.sleep(0.3)
os.close(master_fd)
p.wait()
print("RAW:", repr(b"".join(chunks)))
print("RC :", p.returncode)

# 2) 子进程是否会向真实 fd1 写东西？给 fd1 接管道对比
