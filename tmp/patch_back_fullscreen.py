# -*- coding: utf-8 -*-
"""按用户要求改回全屏：去掉 inline 模式及其配套的 Screen 高度补丁。

（画面错位的真凶是 COLUMNS 环境变量改写，那部分已经修掉，与 inline 无关。）

用法：python3 tmp/patch_back_fullscreen.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "bin", "ai_tui.py")

INLINE_HEIGHT = ("            if getattr(self, \"is_inline\", False):\n"
                 "                # inline 模式下 App 高度取「Screen 内容高度」，而 #log 是 1fr\n"
                 "                # → 会被算成 0（日志区什么都看不到）。给 Screen 一个确定高度。\n"
                 "                try:\n"
                 "                    self.screen.styles.height = max(12, min(24, self.size.height - 2))\n"
                 "                except Exception:\n"
                 "                    pass\n")

PAIRS = [
    ("        app.run(inline=True)\n", "        app.run()\n"),
    (INLINE_HEIGHT, ""),
]


def main() -> int:
    with io.open(TARGET, "r", encoding="utf-8") as f:
        src = f.read()
    for i, (old, new) in enumerate(PAIRS, 1):
        cnt = src.count(old)
        if cnt != 1:
            print(f"❌ 第 {i} 处：匹配 {cnt} 次")
            return 1
        src = src.replace(old, new)
    with io.open(TARGET + ".tmp", "w", encoding="utf-8") as f:
        f.write(src)
    os.replace(TARGET + ".tmp", TARGET)
    print("✅ 已改回全屏（app.run()），并移除 inline 专用的 Screen 高度补丁")
    return 0


if __name__ == "__main__":
    sys.exit(main())
