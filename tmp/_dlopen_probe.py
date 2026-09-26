# -*- coding: utf-8 -*-
"""探测：Termux 下哪些目录可以 dlopen（验证外部存储 /sdcard 被 linker namespace 拒绝）。"""
import ctypes
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

src = os.path.join(ROOT, "lib", "c", "resolve_path", "arm64.so")
print("src =", src, os.path.exists(src))
print("realpath =", os.path.realpath(src))

cands = [
    "/storage/emulated/0/abPython/PythonProject/工具/Hacker--V1.00.1/src/Hacker/onyx-test/home/u0_a305/onyx/onyx/tmp/libtest",
    "/data/data/com.termux/files/home/tmp/onyx_libtest",
    "/data/data/com.termux/files/home/.onyx/lib",
    "/data/data/com.termux/files/usr/lib",
    "/data/data/com.termux/files/usr/tmp/onyx_libtest",
    "/data/data/com.termux/files/usr/var/lib/onyx",
]

for d in cands:
    try:
        os.makedirs(d, exist_ok=True)
        dst = os.path.join(d, "arm64.so")
        shutil.copy(src, dst)
        try:
            ctypes.CDLL(dst)
            print(f"OK    {d}")
        except Exception as e:
            print(f"FAIL  {d} -> {e}")
    except Exception as e:
        print(f"ERR   {d} -> {e}")
