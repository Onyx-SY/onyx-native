# -*- coding: utf-8 -*-
"""打印指定模式在 library 中的上下文样本。"""
import os
import sys

ROOT = "."
PATTERNS = ["edit_file —", "文件不存在: //", "SEARCH text is empty",
            "File not found:", "缺少参数", "validate_edit —"]

hits = {p: [] for p in PATTERNS}
for dirpath, _d, files in os.walk(ROOT):
    if "__pycache__" in dirpath:
        continue
    for fn in files:
        if not fn.endswith((".txt", ".jsonl")):
            continue
        p = os.path.join(dirpath, fn)
        try:
            lines = open(p, encoding="utf-8", errors="replace").read().split("\n")
        except Exception:
            continue
        for i, ln in enumerate(lines):
            for pat in PATTERNS:
                if pat in ln and len(hits[pat]) < 4:
                    hits[pat].append((p, i, lines[max(0, i - 1):i + 2]))

for pat in PATTERNS:
    print(f"\n########## {pat} ##########")
    if not hits[pat]:
        print("  (无)")
    for p, i, ctx in hits[pat]:
        print(f"--- {p}:{i} ---")
        for c in ctx:
            print("   " + c[:200])
