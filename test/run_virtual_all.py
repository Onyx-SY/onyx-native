#!/usr/bin/env python3
"""Run all virtual tests, print per-file results.

判定规则（修正版）：
  - 直接以**脚本**方式运行每个文件（本目录的用例是混合风格）：
      · 脚本式：末尾打印 "ALL PASS"
      · unittest 式：`unittest.main()` → 输出 "Ran N tests" + "OK"
  - 只有「退出码 0」**且**出现上面任一个成功标志才算通过。
  - 旧实现用 `python -m unittest <file>` 收集：脚本式文件会被判成 "NO TESTS RAN"
    并返回非 0 → 整个套件恒为「全 FAIL」，等于这些用例从来没被真正跑过。
"""
import os
import re
import subprocess
import sys

base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "virtual")
files = sorted(f for f in os.listdir(base) if f.startswith("test_") and f.endswith(".py"))

_RAN = re.compile(r"Ran (\d+) tests?")
passed, failed, empty = [], [], []

for f in files:
    path = os.path.join(base, f)
    try:
        r = subprocess.run([sys.executable, path], capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        failed.append(f)
        print(f"[FAIL] {f}: TIMEOUT(600s)")
        continue
    out = (r.stdout or "") + (r.stderr or "")
    ran = _RAN.search(out)
    ok_flag = ("ALL PASS" in out) or (ran and "OK" in out.split("Ran", 1)[-1])
    if r.returncode != 0:
        failed.append(f)
        tail = " | ".join(out.strip().splitlines()[-2:])
        print(f"[FAIL] {f}: rc={r.returncode} {tail}")
    elif not ok_flag:
        empty.append(f)
        print(f"[SKIP] {f}: 无成功标志（可能没有可执行断言）")
    else:
        passed.append(f)
        tag = f"{ran.group(1)} tests" if ran else "script"
        print(f"[OK  ] {f}: {tag}")

print(f"\n通过 {len(passed)} / 失败 {len(failed)} / 无断言 {len(empty)}")
if failed:
    print("FAILED: " + ", ".join(failed))
sys.exit(1 if failed else 0)
