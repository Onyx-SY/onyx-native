# -*- coding: utf-8 -*-
"""Stage3 补丁 C：计划弹窗正文区高度计算修正（边框占用 + 屏幕上限）。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TUI = os.path.join(ROOT, "bin", "ai_tui.py")

OLD = """            # 正文区高度自适应：屏幕越大 / 计划越长，展示越多；手机上也不会把选项挤出屏幕。
            try:
                lines = self.body.count("\\n") + 1
                screen_h = int(getattr(self.app.size, "height", 24) or 24)
                avail = max(3, screen_h - 11)          # 消息 + 标题 + 选项 + 边框内边距
                h = max(3, min(lines + 1, 18, avail))
                self.query_one("#modal-body").styles.height = h
            except Exception:
                pass"""

NEW = """            # 正文区高度自适应：屏幕越大 / 计划越长，展示越多；手机上也不会把选项挤出屏幕。
            # 弹窗整体受 max-height:88% 限制，固定开销 = 边框2 + 内边距2 + 消息1 + 标题1
            # + 正文边框2 + 选项3 = 11 行；正文区 styles.height 含自身上下边框，故再 +2。
            try:
                lines = self.body.count("\\n") + 1
                screen_h = int(getattr(self.app.size, "height", 24) or 24)
                avail = max(3, int(screen_h * 0.88) - 11)
                content_h = max(3, min(lines + 1, 18, avail))
                self.query_one("#modal-body").styles.height = content_h + 2
            except Exception:
                pass"""

with open(TUI, encoding="utf-8") as f:
    text = f.read()
n = text.count(OLD)
if n != 1:
    print(f"FAIL: 命中 {n} 次")
    sys.exit(1)
with open(TUI, "w", encoding="utf-8") as f:
    f.write(text.replace(OLD, NEW, 1))
print("PATCH_OK")
