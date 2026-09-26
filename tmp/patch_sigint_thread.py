# -*- coding: utf-8 -*-
"""修复 TUI 下「AI 请求失败: signal only works in main thread of the main interpreter」。

原因：TUI 的 AI 调用跑在工作线程（bin/ai_tui.py 的 _worker_loop），而
bin/ai_cmd.py 在发起请求前会 `signal.signal(SIGINT, ...)` 安装 Ctrl+C 中断处理器 ——
Python 只允许主线程改信号处理器，工作线程调用直接抛 ValueError，被上层捕获后
显示成「AI 请求失败: ...」。

修法：安装前判断是否主线程；工作线程跳过安装（TUI 的中断交给 Textual：Ctrl+Q/Ctrl+D）。
恢复处（finally 里的 signal.signal）已有 try/except，保持不变。

用法：python3 tmp/patch_sigint_thread.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "bin", "ai_cmd.py")

OLD = "        _signal.signal(_signal.SIGINT, _interrupt_handler)\n"

NEW = (
    "        # ⚠️ 只有主线程能改信号处理器：TUI 模式的 AI 调用跑在工作线程，\n"
    "        # 这里直接 signal.signal() 会抛 ValueError(\"signal only works in main thread\n"
    "        # of the main interpreter\")，表现为界面上「AI 请求失败: signal only works ...」。\n"
    "        # 工作线程里跳过安装（TUI 的中断由 Textual 处理：Ctrl+Q / Ctrl+D）。\n"
    "        if threading.current_thread() is threading.main_thread():\n"
    "            try:\n"
    "                _signal.signal(_signal.SIGINT, _interrupt_handler)\n"
    "            except Exception:\n"
    "                pass\n"
)


def main() -> int:
    with io.open(TARGET, "r", encoding="utf-8") as f:
        src = f.read()
    if src.count(OLD) != 1:
        print(f"❌ 匹配 {src.count(OLD)} 次（要求 1 次）")
        return 1
    src = src.replace(OLD, NEW)
    tmp = TARGET + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(src)
    os.replace(tmp, TARGET)
    print(f"✅ 已加固 SIGINT 安装（仅主线程）→ {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
