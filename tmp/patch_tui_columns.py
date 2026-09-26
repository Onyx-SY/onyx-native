# -*- coding: utf-8 -*-
"""step-1（续）：把 Rich 的绘制宽度对齐到 TUI 日志区宽度。

Rich 的 Console.size 读的是真实终端宽度（COLUMNS），而 #log 比整屏窄（宽屏还要减
38 列侧栏）→ 任何 Rich 渲染（Markdown 换行、表格）都会按整屏宽排，落进日志区被截断。
这里在 mount / resize 时把 COLUMNS 设为 #log 的可用宽度。

用法：python3 tmp/patch_tui_columns.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "bin", "ai_tui.py")

PAIRS = [
    (
        "            self._set_wide(self.size.width >= 100)\n"
        "            threading.Thread(target=self._worker_loop, daemon=True).start()\n",
        "            self._set_wide(self.size.width >= 100)\n"
        "            self._sync_rich_width()\n"
        "            threading.Thread(target=self._worker_loop, daemon=True).start()\n",
    ),
    (
        "        def on_resize(self, event):\n"
        "            self._set_wide(event.size.width >= 100)\n",
        "        def on_resize(self, event):\n"
        "            self._set_wide(event.size.width >= 100)\n"
        "            self._sync_rich_width()\n"
        "\n"
        "        def _sync_rich_width(self):\n"
        "            \"\"\"把 Rich 的绘制宽度对齐到日志区宽度。\n"
        "\n"
        "            Rich 的 Console.size 只认真实终端宽度（COLUMNS），而 #log 比整屏窄\n"
        "            （宽屏要减 38 列侧栏）→ 面板/表格按整屏宽排版，落进日志区就被换行截断。\n"
        "            \"\"\"\n"
        "            try:\n"
        "                w = self.query_one(\"#log\").content_size.width\n"
        "                if w > 8:\n"
        "                    os.environ[\"COLUMNS\"] = str(w)\n"
        "                    os.environ[\"LINES\"] = str(max(10, self.size.height))\n"
        "            except Exception:\n"
        "                pass\n",
    ),
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
    tmp = TARGET + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(src)
    os.replace(tmp, TARGET)
    print(f"✅ 已应用 {len(PAIRS)} 处替换 → {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
