#!/usr/bin/env python3
"""多行命令完整性检测测试：粘贴整块/单行自闭合不应再进入续行模式。"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from lib.terminal.input_lib import _multiline_text_complete

CASES = [
    # (文本, 语法, 期望结果, 说明)
    # ── 应判定为完整（不再续行）──
    ("if [ -f x ]; then\n    echo y\nfi", "bash", True, "粘贴完整 if/fi 块"),
    ("if x; then y; fi", "bash", True, "单行自闭合 if"),
    ("for i in x; do echo; done", "bash", True, "单行自闭合 for"),
    ("while true; do sleep 1; done", "bash", True, "单行自闭合 while"),
    ("case $x in a) echo;; esac", "bash", True, "单行自闭合 case"),
    ("cat <<EOF\nhello\nEOF", "bash", True, "粘贴完整 heredoc"),
    ("cat <<-TAG\nhi\n\tTAG", "bash", True, "粘贴完整 heredoc（去 tab）"),
    ("echo a\necho b", "bash", True, "普通多行无结构"),
    ("def foo():\n    return 1", "python", True, "粘贴完整 python 函数"),
    ("if x:\n    print(1)", "python", True, "粘贴完整 python if"),
    ("for i in range(3):\n    print(i)", "python", True, "粘贴完整 python for"),
    # ── 应判定为未完成（继续续行）──
    ("if x; then", "bash", False, "if 未闭合"),
    ("if x; then\n    echo y", "bash", False, "粘贴缺 fi"),
    ("cat <<EOF", "bash", False, "heredoc 无结束符"),
    ("cat <<EOF\nhello", "bash", False, "heredoc 缺 EOF"),
    ("for i in x; do", "bash", False, "for 未闭合"),
    ("def foo():", "python", False, "python 函数缺函数体"),
    ("if x:", "python", False, "python if 缺函数体"),
    ("x = [1,", "python", False, "python 行尾括号未闭合"),
    ("x = [1,\n2, 3]", "python", True, "粘贴完整 python 列表"),
]

failed = 0
for text, syntax, expected, desc in CASES:
    got = _multiline_text_complete(text, syntax)
    status = "✅" if got == expected else "❌"
    if got != expected:
        failed += 1
    print(f"{status} [{syntax:6}] {desc:28} → {'完整' if got else '续行'} (期望 {'完整' if expected else '续行'})")
    if got != expected:
        print(f"     文本: {text!r}")

print()
if failed:
    print(f"失败 {failed} 项 ❌")
    sys.exit(1)
print("全部通过 ✅")
