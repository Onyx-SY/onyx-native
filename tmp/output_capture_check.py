# -*- coding: utf-8 -*-
"""验证：capture_command_output 的实时回显写到 sys.__stdout__（真实终端）而不是当前 sys.stdout。

TUI 里当前 sys.stdout 是队列流（→ RichLog），写 sys.__stdout__ 会绕过界面、
直接糊在 Textual 画面上。本脚本用"假 stdout"模拟 TUI 场景。

用法：python3 tmp/output_capture_check.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bin.ai_lib.output_capture import capture_command_output  # noqa: E402


class FakeStream:
    """模拟 TUI 的队列流：内容应进入这里（→ RichLog）。"""

    def __init__(self):
        self.data = []

    def write(self, s):
        self.data.append(s)

    def flush(self):
        pass

    def isatty(self):
        return False


fake = FakeStream()
orig = sys.stdout
sys.stdout = fake                      # 模拟 TUI 里 _run_one 换上的队列流
try:
    with capture_command_output() as (out_catcher, _err):
        out_catcher._ai_triggered = True
        sys.stdout.write("MARKER-TOOL-OUTPUT\n")   # 工具执行时的输出
finally:
    sys.stdout = orig

captured = "".join(fake.data)
print(f"→ 队列流(应为 True)：{'MARKER-TOOL-OUTPUT' in captured}")
print(f"→ 当前 sys.stdout 收到：{captured!r}")
print("→ 若上面终端里另外出现了一行 MARKER-TOOL-OUTPUT，说明它绕过了队列流直接糊到了真实终端（TUI 画面会被破坏）")
