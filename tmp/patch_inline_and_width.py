# -*- coding: utf-8 -*-
"""修复 + inline 模式：

【修复】去掉 os.environ["COLUMNS"]/["LINES"] 的写法。
  Textual 驱动用 shutil.get_terminal_size() 判断终端尺寸，而它会读 COLUMNS/LINES
  → 改环境变量 = 骗 Textual「终端只有日志区那么窄」→ 侧栏/日志按错误宽度排版，
  画面糊成一团（用户截图里的错位就是这么来的）。
  改为只设置 AI 输出用到的 Rich Console 宽度（不动进程环境变量）。

【inline】app.run(inline=True)：不再接管整个屏幕（不用 alt-screen），
  只在终端底部占一块区域渲染自己的控件，上方保留 shell 滚动历史。

用法：python3 tmp/patch_inline_and_width.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "bin", "ai_tui.py")

OLD_WIDTH = '''        def _sync_rich_width(self):
            """把 Rich 的绘制宽度对齐到日志区宽度。

            Rich 的 Console.size 只认真实终端宽度（COLUMNS），而 #log 比整屏窄
            （宽屏要减 38 列侧栏）→ 面板/表格按整屏宽排版，落进日志区就被换行截断。
            """
            try:
                w = self.query_one("#log").content_size.width
                if w > 8:
                    os.environ["COLUMNS"] = str(w)
                    os.environ["LINES"] = str(max(10, self.size.height))
            except Exception:
                pass
'''

NEW_WIDTH = '''        def _sync_rich_width(self):
            """把 AI 输出的 Rich 绘制宽度对齐到日志区宽度。

            ⚠️ 只改 Console 的宽度，**绝不改 os.environ["COLUMNS"]**：
            Textual 驱动用 shutil.get_terminal_size() 判断终端尺寸，而它会读
            COLUMNS/LINES 环境变量 —— 改环境变量等于骗 Textual「终端只有日志区那么窄」，
            侧栏/日志会按错误宽度排版，整个画面错位糊掉（真踩过这个坑）。

            日志区比整屏窄（宽屏要减侧栏），不改宽度的话 AI 的 Markdown 会按整屏宽换行。
            """
            try:
                w = self.query_one("#log").content_size.width
                if w <= 8:
                    return
                h = max(10, self.size.height)
                for mod_name in ("bin.ai_lib.ui", "bin.ai_cmd"):
                    mod = sys.modules.get(mod_name)
                    console = getattr(mod, "console", None) if mod is not None else None
                    if console is not None:
                        # 与 Rich Console(width=…, height=…) 构造参数等价
                        console._width, console._height = w, h
            except Exception:
                pass
'''

PAIRS = [
    (OLD_WIDTH, NEW_WIDTH),
    ("        app.run()\n", "        app.run(inline=True)\n"),
]


def main() -> int:
    with io.open(TARGET, "r", encoding="utf-8") as f:
        src = f.read()
    for i, (old, new) in enumerate(PAIRS, 1):
        cnt = src.count(old)
        if cnt != 1:
            print(f"❌ 第 {i} 处：匹配 {cnt} 次（要求 1 次）")
            return 1
        src = src.replace(old, new)
    with io.open(TARGET + ".tmp", "w", encoding="utf-8") as f:
        f.write(src)
    os.replace(TARGET + ".tmp", TARGET)
    print("✅ 已去掉 COLUMNS 环境变量改写 + 改用 Rich Console 宽度；TUI 改 inline 模式")
    return 0


if __name__ == "__main__":
    sys.exit(main())
