#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""临时冒烟测试：AI 一次性 PTY 执行（Linux 本机验证）"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.terminal import exe

print("AI_PTY_AVAILABLE:", exe._AI_PTY_AVAILABLE)

# 1. 清洗函数
noisy = "\x1b[31mred\x1b[0m \x1b[?2004h\x1b]0;title\x07plain\r\nnext\r\x1b[32mgreen\x1b[39m\x08"
stripped = exe._strip_terminal_noise(noisy)
print("strip:", repr(stripped))
assert stripped == "red plain\nnext\ngreen", repr(stripped)

def run(cmd):
    exe.AI_EXECUTION_MODE = True
    exe.AI_LAST_EXIT_CODE = None
    cache = {}
    rc = exe.run_cmd_sync(cmd, "test-rid", is_tool=True, AI_TOOL_OUTPUT_CACHE=cache)
    exe.AI_EXECUTION_MODE = False
    print(f"cmd={cmd!r} rc={rc} last={exe.AI_LAST_EXIT_CODE} cache={cache!r}")
    return rc, cache.get("test-rid")

# 2. 颜色命令 → 清洗后干净
rc, out = run(r"printf '\033[31mred\033[0m\nplain'")
assert rc == 0 and out == "red\nplain", (rc, out)

# 3. 真实退出码
rc, out = run("exit 3")
assert rc == 3 and exe.AI_LAST_EXIT_CODE == 3, (rc, out)

# 4. TTY 检测（subprocess 下是 not a tty；PTY 下应为 /dev/pts/N）
rc, out = run("tty")
assert rc == 0 and out and "/dev/pts/" in out, (rc, out)

# 5. 管道 + stderr 合并
rc, out = run("echo out; echo err 1>&2")
assert rc == 0, rc
print("merged:", repr(out))

# 6. 后台任务不悬挂（排空宽限 2s 内返回）
rc, out = run("echo hi; sleep 3 &")
assert rc == 0, rc
print("bg:", repr(out))

# 7. 无输出命令 → "[No output]"
rc, out = run("true")
assert rc == 0 and out == "[No output]", (rc, out)

print("ALL OK")
