# -*- coding: utf-8 -*-
"""通用精确替换补丁：python3 tmp/patch.py <target> <old_file> <new_file>

- 读取 target，断言 old 出现且仅出现一次（--all 允许多次），替换后写回。
- old/new 从文件读取，避免 shell/JSON 转义问题。
"""
import sys

def main():
    args = [a for a in sys.argv[1:] if a != "--all"]
    allow_all = "--all" in sys.argv
    target, old_f, new_f = args[0], args[1], args[2]
    with open(target, encoding="utf-8") as f:
        src = f.read()
    with open(old_f, encoding="utf-8") as f:
        old = f.read()
    with open(new_f, encoding="utf-8") as f:
        new = f.read()
    n = src.count(old)
    if n == 0:
        print("FAIL: old not found"); sys.exit(2)
    if n > 1 and not allow_all:
        print(f"FAIL: old occurs {n} times"); sys.exit(3)
    src = src.replace(old, new) if allow_all else src.replace(old, new, 1)
    with open(target, "w", encoding="utf-8") as f:
        f.write(src)
    print(f"OK: replaced {n if allow_all else 1} occurrence(s)")

if __name__ == "__main__":
    main()
