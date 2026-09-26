# -*- coding: utf-8 -*-
"""inline 模式适配：给 Screen 一个确定高度。

inline 下 App 的高度取「Screen 的内容高度」（Screen._get_inline_height →
get_content_height），而 #log 是 `height: 1fr` → 内容高度算成 0 →
日志区拿不到任何高度（消息看不见，只剩提示行）。给 Screen 设固定高度即可。

用法：python3 tmp/patch_inline_height.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "bin", "ai_tui.py")

OLD = "            self._set_wide(self.size.width >= 100)\n            self._sync_rich_width()\n"
NEW = ("            self._set_wide(self.size.width >= 100)\n"
       "            self._sync_rich_width()\n"
       "            if getattr(self, \"is_inline\", False):\n"
       "                # inline 模式下 App 高度取「Screen 内容高度」，而 #log 是 1fr\n"
       "                # → 会被算成 0（日志区什么都看不到）。给 Screen 一个确定高度。\n"
       "                try:\n"
       "                    self.screen.styles.height = max(12, min(24, self.size.height - 2))\n"
       "                except Exception:\n"
       "                    pass\n")


def main() -> int:
    with io.open(TARGET, "r", encoding="utf-8") as f:
        src = f.read()
    if src.count(OLD) != 1:
        print(f"❌ 匹配 {src.count(OLD)} 次")
        return 1
    src = src.replace(OLD, NEW)
    with io.open(TARGET + ".tmp", "w", encoding="utf-8") as f:
        f.write(src)
    os.replace(TARGET + ".tmp", TARGET)
    print("✅ 已为 inline 模式设置 Screen 高度")
    return 0


if __name__ == "__main__":
    sys.exit(main())
