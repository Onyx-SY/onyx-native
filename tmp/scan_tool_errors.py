# -*- coding: utf-8 -*-
"""扫描 library/日志，统计实际出现过的工具错误签名。"""
import os
import re
import sys
from collections import Counter

ROOTS = sys.argv[1:] or ["."]

# 常见工具错误签名
SIGS = [
    "File not found", "Not a file", "SEARCH text not found", "not unique",
    "SEARCH text is empty", "Read failed", "缺少参数", "Missing parameter",
    "edit_file failed", "validate_edit failed", "write_file failed",
    "preview_edit failed", "文件不存在", "Backup failed", "Write failed",
    "Read failed", "unknown tool", "not found", "invalid", "Invalid",
    "Error", "Traceback", "参数", "❌",
]

counts = Counter()
examples = {}
generic = Counter()

for root in ROOTS:
    for dirpath, _dirs, files in os.walk(root):
        if "__pycache__" in dirpath:
            continue
        for fn in files:
            if not (fn.endswith(".txt") or fn.endswith(".jsonl") or fn.endswith(".log")):
                continue
            p = os.path.join(dirpath, fn)
            try:
                data = open(p, encoding="utf-8", errors="replace").read()
            except Exception:
                continue
            for s in SIGS:
                n = data.count(s)
                if n:
                    counts[s] += n
                    examples.setdefault(s, p)
            for m in re.finditer(r"❌ [^\n\"]{0,70}", data):
                generic[m.group(0).strip()] += 1

print("=== 错误签名计数（全 library/日志）===")
for s, n in counts.most_common():
    print(f"  {n:6d}  {s}")
print("\n=== 最常见的 '❌ ...' 具体消息 TOP 40 ===")
for s, n in generic.most_common(40):
    print(f"  {n:6d}  {s}")
