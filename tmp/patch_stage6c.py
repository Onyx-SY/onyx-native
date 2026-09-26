# -*- coding: utf-8 -*-
"""Stage6 补丁 C：用「最近程序化写入值集合」判定，而不是单个值。

异步 Changed 可能**晚于**下一次程序化写入才被处理（值已经变了），
单值比对会把它误判成用户编辑 → 历史游标又被复位。保留最近 16 个值即可。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TUI = os.path.join(ROOT, "bin", "ai_tui.py")

P = [
    (
        """            self._hist_prog_value = None  # 最近一次「程序化写入」的值（见 _set_input_value）""",
        """            # 最近若干次「程序化写入」的值（见 _set_input_value / on_input_changed）：
            # Textual 的 Changed 是异步投递的，可能晚到好几拍，单值比对会漏。
            self._hist_prog_values = []""",
    ),
    (
        """            # Textual 的 Changed 是异步投递的 → 标志位挡不住，必须靠「值比对」
            # （见 on_input_changed）：记下这次程序化写入的值，供异步事件识别。
            self._hist_prog_value = value""",
        """            # Textual 的 Changed 是异步投递的 → 标志位挡不住，必须靠「值比对」
            # （见 on_input_changed）：记下这次程序化写入的值，供异步事件识别。
            self._hist_prog_values.append(value)
            if len(self._hist_prog_values) > 16:
                del self._hist_prog_values[:-16]""",
    ),
    (
        """                val = getattr(event, "value", None)
                _prog = val is not None and val == getattr(self, "_hist_prog_value", None)""",
        """                val = getattr(event, "value", None)
                _prog = val is not None and val in getattr(self, "_hist_prog_values", ())""",
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
