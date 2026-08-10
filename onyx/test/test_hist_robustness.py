# -*- coding: utf-8 -*-
"""最终健壮性检查：真实历史文件内容（含历史遗留脏条目）下导航不崩溃、行为正确"""
import io
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib.terminal import input_lib as il

# 从用户真实历史文件提取的典型条目（含历史遗留脏数据）
REAL_LIKE = [
    '{"multiline": true, "cmd": "cat > a.txt << EOF\\n12\\nls\\nbd\\nEOF"}',  # JSON 存储（未解码状态）
    "cat > a.txt << EOF",                      # 历史遗留：被截断的首行
    '{"multiline": true, "cmd": "cat > a.txt << EOF\\nls\\ncd solar-system/\\nls\\nEOF"}',
    "cat > app/templates/index.html << EOF l o w EOF\x00\x00EOF",  # 旧 NUL 脏数据
    "ls \\\nab",                                # 行续符多行
    "for i in 1 2 3; do\n  echo $i\ndone",
    "ls -la",
]

# 模拟 _load_history_buffer 的解码
decoded = []
for line in REAL_LIKE:
    d = il._decode_multiline_from_storage(line)
    d = il._clean_display_text(d)
    decoded.append(d)

il._HISTORY_BUFFER = decoded
il._HISTORY_INITIALIZED = True
il.reset_history_index()

print("=== 解码后的历史 ===")
for i, e in enumerate(il._HISTORY_BUFFER):
    print(f"  [{i}] {e!r}")

print("\n=== 连续 Up × 7（遍历全部）===")
cur = ""
for step in range(7):
    cur, pos = il.handle_up_arrow_normal(cur)
    print(f"  Up{step+1}: {cur!r}")
up_results = []

print("\n=== Down 回到起点 ===")
cur2 = cur
for step in range(8):
    cur2, pos = il.handle_down_arrow_normal(cur2)
    print(f"  Down{step+1}: {cur2!r}")
    up_results.append(cur2)
assert cur2 == "", "Down 应回到起点空输入"

print("\n=== 提交 Up1（最新 heredoc）===")
il.reset_history_index()
cur, pos = il.handle_up_arrow_normal("")
user_input = cur
if il._NAVIGATION_RAW_COMMAND is not None:
    display_form = il._format_history_for_display(il._NAVIGATION_RAW_COMMAND)
    if user_input == display_form:
        user_input = il._NAVIGATION_RAW_COMMAND
    il._NAVIGATION_RAW_COMMAND = None
print(f"  提交: {user_input!r}")
assert "\n" in user_input, "应提交完整多行 heredoc"
assert user_input == "cat > a.txt << EOF\n12\nls\nbd\nEOF", f"内容不符: {user_input!r}"

# 完整多行交给 _process_multiline_input 直接通过
calls = {"n": 0}
_orig = il.prompt
def fake(*a, **k):
    calls["n"] += 1
    raise EOFError()
il.prompt = fake
try:
    r = il._process_multiline_input(user_input)
    assert r is None and calls["n"] == 0, "完整多行不应进入续行循环"
finally:
    il.prompt = _orig
print("  完整多行直接通过 ✓")

print("\nALL ROBUSTNESS CHECKS PASSED")
