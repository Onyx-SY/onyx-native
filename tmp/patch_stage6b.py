# -*- coding: utf-8 -*-
"""Stage6 补丁 B：修复「翻历史后被异步 Changed 复位」的真 bug。

Textual 的 Input.Changed 是**异步**投递的（post_message），所以 `_set_input_value` 里
用 `_hist_setting` 标志位挡不住它：程序化写入历史后，下一个事件循环回合 Changed 才到，
此时标志已复位 → `on_input_changed` 把 `_hist_idx` 复位成 len(_hist)。
后果：连按 ↑ 无法继续往更旧走（每次都从最新一条重新开始）。
这里改用「值比对」判定：Changed 携带的值 == 我们刚程序化写入的值 → 不算用户编辑。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TUI = os.path.join(ROOT, "bin", "ai_tui.py")

P = [
    (
        """            self._menu_cache = {}        # 补全候选缓存（避免同一文本重复扫盘）""",
        """            self._menu_cache = {}        # 补全候选缓存（避免同一文本重复扫盘）
            self._hist_prog_value = None  # 最近一次「程序化写入」的值（见 _set_input_value）""",
    ),
    (
        """            self._hist_setting = True
            try:
                if isinstance(w, PromptArea):
                    w.text = value
                else:
                    w.value = value
                try:
                    w.cursor_position = len(value)
                except Exception:
                    pass
            except Exception:
                pass
            finally:
                self._hist_setting = False""",
        """            self._hist_setting = True
            try:
                if isinstance(w, PromptArea):
                    w.text = value
                else:
                    w.value = value
                try:
                    w.cursor_position = len(value)
                except Exception:
                    pass
            except Exception:
                pass
            finally:
                self._hist_setting = False
            # Textual 的 Changed 是异步投递的 → 标志位挡不住，必须靠「值比对」
            # （见 on_input_changed）：记下这次程序化写入的值，供异步事件识别。
            self._hist_prog_value = value""",
    ),
    (
        """            try:
                if not getattr(self, "_hist_setting", False) and self._hist_idx != len(self._hist):
                    self._hist_idx = len(self._hist)
            except Exception:
                pass""",
        """            try:
                # 程序化写入（翻历史 / 滑动回滚）触发的异步 Changed：值与我们刚写入的
                # 完全一致 → 不是用户编辑，绝不能复位历史游标（否则连按 ↑ 永远停在最新一条）。
                val = getattr(event, "value", None)
                _prog = val is not None and val == getattr(self, "_hist_prog_value", None)
                if not _prog and not getattr(self, "_hist_setting", False) \\
                        and self._hist_idx != len(self._hist):
                    self._hist_idx = len(self._hist)
            except Exception:
                pass""",
    ),
]


def main():
    with open(TUI, encoding="utf-8") as f:
        text = f.read()
    for old, new in P:
        n = text.count(old)
        if n != 1:
            print(f"FAIL: 命中 {n} 次\n{old[:200]}")
            return 1
        text = text.replace(old, new, 1)
    with open(TUI, "w", encoding="utf-8") as f:
        f.write(text)
    print("PATCH_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
