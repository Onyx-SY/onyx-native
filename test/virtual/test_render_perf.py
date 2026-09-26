#!/usr/bin/env python3
"""渲染性能与正确性回归（_QueueStream 批量切分 / 日志容量上限）。

运行: python3 test/virtual/test_render_perf.py
"""
import os
import queue
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from bin.ai_tui import _QueueStream  # noqa: E402


def test_line_splitting_correct():
    q = queue.Queue()
    s = _QueueStream(q)
    s.write("a\nb\nc")          # 末尾半行留在缓冲
    s.write("-d\n")             # 与缓冲拼成 "c-d"
    s.flush()
    got = []
    while not q.empty():
        got.append(q.get_nowait())
    assert got == ["a", "b", "c-d"], got
    print(f"PASS 分行正确（含跨写入的半行拼接）：{got}")


def test_no_newline_fast_path():
    q = queue.Queue()
    s = _QueueStream(q)
    s.write("no-newline")
    assert q.empty(), "无换行时不应投递"
    s.flush()
    assert q.get_nowait() == "no-newline"
    print("PASS 无换行时走快速路径（不投递）")


def test_large_write_throughput():
    q = queue.Queue()
    s = _QueueStream(q)
    lines = 100_000
    chunk = "\n".join(f"line-{i}" for i in range(lines)) + "\n"
    t0 = time.perf_counter()
    for _ in range(1):
        s.write(chunk)
    elapsed = time.perf_counter() - t0
    n = q.qsize()
    assert n == lines, f"投递行数不符：{n} != {lines}"
    assert elapsed < 3.0, f"10 万行写入过慢：{elapsed:.3f}s"
    print(f"PASS 10 万行写入 {elapsed:.3f}s（{lines} 行，上限 3s）")


def test_many_small_writes():
    q = queue.Queue()
    s = _QueueStream(q)
    t0 = time.perf_counter()
    for i in range(20_000):
        s.write(f"x{i}\n")
    elapsed = time.perf_counter() - t0
    assert q.qsize() == 20_000
    assert elapsed < 3.0, f"2 万次小写入过慢：{elapsed:.3f}s"
    print(f"PASS 2 万次小写入 {elapsed:.3f}s")


def test_richlog_max_lines_configured():
    src = open(os.path.join(ROOT, "bin", "ai_tui.py"), encoding="utf-8").read()
    assert "max_lines=5000" in src, "RichLog 应设置 max_lines 限制历史行数"
    print("PASS RichLog 已限制历史行数（max_lines=5000）")


if __name__ == "__main__":
    test_line_splitting_correct()
    test_no_newline_fast_path()
    test_large_write_throughput()
    test_many_small_writes()
    test_richlog_max_lines_configured()
    print("\nALL PASS")
