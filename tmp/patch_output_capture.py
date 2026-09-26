# -*- coding: utf-8 -*-
"""额外修复（子代理审计第 3 条，已实测复现）：TUI 里工具输出绕过 RichLog 糊在画面上。

`RealTimeOutputCatcher` 的实时回显固定写 `sys.__stdout__`（真实终端 fd）。
REPL 下没问题（当前 stdout 就是真实终端），但 TUI 下 `_run_one` 已把 sys.stdout
换成队列流（→ RichLog），写 sys.__stdout__ 等于绕过界面直接往 Textual 画面上刷字符。

修法：捕获开始时记住"当前" stdout/stderr（live_stream），回显写它；取不到再退
回 sys.__stdout__/__stderr__，REPL 行为完全不变。

用法：python3 tmp/patch_output_capture.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "bin", "ai_lib", "output_capture.py")

PAIRS = [
    (
        'class RealTimeOutputCatcher:\n'
        '    def __init__(self, stream_type):\n'
        '        self.stream_type = stream_type\n'
        '        self.buffer = []\n',
        'class RealTimeOutputCatcher:\n'
        '    def __init__(self, stream_type, live_stream=None):\n'
        '        # live_stream：捕获开始时的「当前」stdout/stderr。\n'
        '        # TUI 下它是队列流（内容会进 RichLog）；REPL 下就是真实终端。\n'
        '        # 不能固定写 sys.__stdout__ —— 那会绕过 TUI 的界面直接糊在 Textual 画面上。\n'
        '        self.stream_type = stream_type\n'
        '        self._live_stream = live_stream\n'
        '        self.buffer = []\n',
    ),
    (
        '        if self.stream_type == "stdout":\n'
        '            self._line_count += message.count(\'\\n\')\n'
        '            if self._ai_triggered and self._line_count > 10:\n'
        '                return  # AI 模式超过10行，停止实时显示\n'
        '            sys.__stdout__.write(message)\n'
        '            sys.__stdout__.flush()\n'
        '        else:\n'
        '            sys.__stderr__.write(message)\n'
        '            sys.__stderr__.flush()\n',
        '        if self.stream_type == "stdout":\n'
        '            self._line_count += message.count(\'\\n\')\n'
        '            if self._ai_triggered and self._line_count > 10:\n'
        '                return  # AI 模式超过10行，停止实时显示\n'
        '        self._write_live(message)\n'
        '\n'
        '    def _write_live(self, message):\n'
        '        """把实时输出写回「捕获开始时的当前流」（TUI=队列流 / REPL=真实终端）。"""\n'
        '        target = self._live_stream\n'
        '        if target is None:\n'
        '            target = sys.__stdout__ if self.stream_type == "stdout" else sys.__stderr__\n'
        '        try:\n'
        '            target.write(message)\n'
        '            target.flush()\n'
        '        except Exception:\n'
        '            pass\n',
    ),
    (
        '    def flush(self):\n'
        '        if self._closed:\n'
        '            return\n'
        '        if self.stream_type == "stdout":\n'
        '            sys.__stdout__.flush()\n'
        '        else:\n'
        '            sys.__stderr__.flush()\n',
        '    def flush(self):\n'
        '        if self._closed:\n'
        '            return\n'
        '        target = self._live_stream\n'
        '        if target is None:\n'
        '            target = sys.__stdout__ if self.stream_type == "stdout" else sys.__stderr__\n'
        '        try:\n'
        '            target.flush()\n'
        '        except Exception:\n'
        '            pass\n',
    ),
    (
        '    stdout_catcher = RealTimeOutputCatcher("stdout")\n'
        '    stderr_catcher = RealTimeOutputCatcher("stderr")\n',
        '    stdout_catcher = RealTimeOutputCatcher("stdout", live_stream=original_stdout)\n'
        '    stderr_catcher = RealTimeOutputCatcher("stderr", live_stream=original_stderr)\n',
    ),
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
    tmp = TARGET + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(src)
    os.replace(tmp, TARGET)
    print(f"✅ 已应用 {len(PAIRS)} 处替换 → {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
