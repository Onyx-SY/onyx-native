# -*- coding: utf-8 -*-
"""Stage2 补丁 B：Console 钩子提前到 on_mount + full 语义修正。

- 钩子装到 on_mount：此后创建的所有业务 Console 都进注册表（引擎模块是惰性 import 的，
  原先要等到第一轮 _run_one 才装钩子 → 中间创建的 console 只能靠全堆扫描兜）。
- _collect_rich_consoles(full=...)：full=True 强制全堆重扫（启动 / 测试用）；
  full=False 走注册表 + 「首次一次」兜底扫描。
- _run_one 改为 full=False：每轮不再全堆扫描（钩子已保证覆盖）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TUI = os.path.join(ROOT, "bin", "ai_tui.py")

PATCHES = [
    # 1) _collect_rich_consoles 接受 full 参数
    (
        """        def _collect_rich_consoles(self):
            \"\"\"收集进程内所有 Rich Console 实例。""",
        """        def _collect_rich_consoles(self, full: bool = False):
            \"\"\"收集进程内所有 Rich Console 实例。""",
    ),
    (
        """                if not self._gc_scanned:""",
        """                if full or not self._gc_scanned:""",
    ),
    # 2) _sync_rich_width 透传 full
    (
        """                if full or not getattr(self, "_rich_consoles", None):
                    self._collect_rich_consoles()""",
        """                if full or not getattr(self, "_rich_consoles", None):
                    self._collect_rich_consoles(full=full)""",
    ),
    # 3) on_mount 提前装钩子
    (
        """            self._banner()
            self._load_history()""",
        """            # 尽早拦 Console 构造器：引擎模块（ai_cmd / tool_executors / …）都是
            # 惰性 import 的，晚装钩子会漏掉「钩子之前」创建的那批 console。
            _install_console_color_hook(self)
            self._banner()
            self._load_history()""",
    ),
    # 4) _run_one 不再每轮全堆扫描
    (
        """                self._sync_rich_width(full=True)
                # 引擎模块是在 _call_ai_engine **内部**才 import 的 → gc 同步追不上，
                # 必须拦 Console 构造器，才能保证首轮的工具输出就带颜色、宽度正确。
                _install_console_color_hook(self)""",
        """                # 钩子已在 on_mount 装好，引擎模块的 console 会自动进注册表 →
                # 这里走注册表路径（不再每轮 gc.get_objects() 全堆扫描）。
                self._sync_rich_width()
                _install_console_color_hook(self)   # 幂等：兜住极端时序""",
    ),
]


def main():
    with open(TUI, encoding="utf-8") as f:
        text = f.read()
    for old, new in PATCHES:
        n = text.count(old)
        if n != 1:
            print(f"FAIL: 命中 {n} 次（期望 1）\n{old[:200]}")
            return 1
        text = text.replace(old, new, 1)
        print(f"OK   {len(old)}B → {len(new)}B")
    with open(TUI, "w", encoding="utf-8") as f:
        f.write(text)
    print("PATCH_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
