# -*- coding: utf-8 -*-
"""修复（第二处）：handle_ai 单次问答分支的 SIGINT 安装同样不能在工作线程执行。

调用栈实测（tmp/dbg_signal_trace.py）：
  _worker_loop → _call_ai_engine → handle_ai → ai_cmd.py:1394
      _signal.signal(_signal.SIGINT, _on_interrupt)   ← ValueError

用法：python3 tmp/patch_sigint_thread2.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "bin", "ai_cmd.py")

PAIRS = [
    # 1) 安装点：仅主线程
    (
        "    _signal.signal(_signal.SIGINT, _on_interrupt)\n",
        "    # ⚠️ 只有主线程能改信号处理器：TUI 的 AI 调用跑在工作线程\n"
        "    # （_worker_loop → _call_ai_engine → handle_ai），直接安装会抛\n"
        "    # ValueError(\"signal only works in main thread of the main interpreter\")，\n"
        "    # 被上层捕获后显示成「AI 请求失败: signal only works ...」。\n"
        "    # 工作线程里跳过（TUI 的中断交给 Textual：Ctrl+Q / Ctrl+D）。\n"
        "    if threading.current_thread() is threading.main_thread():\n"
        "        try:\n"
        "            _signal.signal(_signal.SIGINT, _on_interrupt)\n"
        "        except Exception:\n"
        "            pass\n",
    ),
    # 2) 恢复点：工作线程里 signal.signal 同样会抛，加 try/except 兜底
    (
        "    # 恢复原始 SIGINT 处理器\n"
        "    import signal as _signal\n"
        "    _signal.signal(_signal.SIGINT, _original_sigint)\n",
        "    # 恢复原始 SIGINT 处理器（工作线程里 signal.signal 不可用 → 容错）\n"
        "    import signal as _signal\n"
        "    try:\n"
        "        _signal.signal(_signal.SIGINT, _original_sigint)\n"
        "    except Exception:\n"
        "        pass\n",
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
