# -*- coding: utf-8 -*-
"""基准：HistorySuggester 的历史加载与逐键前缀查找开销（真实历史文件）。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bin.ai_tui import _ai_history_path  # noqa: E402

p = _ai_history_path(os.path.expanduser("~"))
print("history:", p)
print("exists:", os.path.exists(p), "size:", os.path.getsize(p) if os.path.exists(p) else 0)

items = []
t0 = time.perf_counter()
with open(p, encoding="utf-8", errors="replace") as f:
    for raw in f:
        line = raw.rstrip("\n")
        if line.startswith("+"):
            line = line[1:]
        line = line.strip()
        if line:
            items.append(line)
t1 = time.perf_counter()
print("加载 %d 条历史：%.1f ms" % (len(items), (t1 - t0) * 1000))


def sug(value):
    for item in reversed(items):
        if item != value and item.startswith(value):
            return item
    return None


t0 = time.perf_counter()
for _ in range(200):
    sug("检查")
t1 = time.perf_counter()
print("单次前缀查找（命中较晚）：%.3f ms" % ((t1 - t0) / 200 * 1000))

t0 = time.perf_counter()
for _ in range(200):
    sug("zzz不存在的前缀")
t1 = time.perf_counter()
print("单次前缀查找（全表扫完）：%.3f ms" % ((t1 - t0) / 200 * 1000))
