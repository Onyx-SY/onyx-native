# -*- coding: utf-8 -*-
"""补文档：把「→ 接受虚影」「/ 立即弹命令列表」写进 REPL /help 的双语提示。

用法：python3 tmp/patch_help_tips.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "bin", "ai_interactive.py")

PAIRS = [
    (
        "- 按 `Enter` 发送；按 `Alt+Enter` 进入多行模式（多行模式下 `Enter` 换行暂存、不会发送，再次按 `Alt+Enter` 统一发送）\n",
        "- 按 `Enter` 发送；按 `Alt+Enter` 进入多行模式（多行模式下 `Enter` 换行暂存、不会发送，再次按 `Alt+Enter` 统一发送）\n"
        "- 输入 `/` 立即弹出命令列表（无需手动 Tab）；`→` 直接接受灰色虚影补全\n",
    ),
    (
        "- Press `Enter` to send; press `Alt+Enter` to enter multiline mode (then `Enter` inserts a newline without sending; press `Alt+Enter` again to send all lines at once)\n",
        "- Press `Enter` to send; press `Alt+Enter` to enter multiline mode (then `Enter` inserts a newline without sending; press `Alt+Enter` again to send all lines at once)\n"
        "- Typing `/` immediately opens the command list (no Tab needed); `→` accepts the grey ghost completion\n",
    ),
]


def main() -> int:
    with io.open(TARGET, "r", encoding="utf-8") as f:
        src = f.read()
    for i, (old, new) in enumerate(PAIRS, 1):
        if src.count(old) != 1:
            print(f"❌ 第 {i} 处匹配 {src.count(old)} 次")
            return 1
        src = src.replace(old, new)
    tmp = TARGET + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(src)
    os.replace(tmp, TARGET)
    print("✅ /help 提示已补充（中英各一行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
