# -*- coding: utf-8 -*-
"""测量 `-c` 单条命令模式（oneshot）启动时到底加载了哪些模块。

做法：完全复用 Onyx.run_command_once 的真实路径，只把「管理员密码」这道
交互闸门打桩跳过（本机未设置密码，真实 CLI 会卡在输入密码）。
"""
import os
import sys
import time

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT_DIR = os.path.dirname(PROJ)
USER_HOME = os.path.join(ROOT_DIR, "home", "u0_a305")

sys.path.insert(0, PROJ)
os.chdir(USER_HOME)          # 模拟用户从 ~ 启动

WATCH = (
    "prompt_toolkit", "textual", "rich", "pygments", "inquirer",
    "msgpack", "readline", "wcwidth",
    "lib.terminal.input_lib", "lib.terminal.com", "lib.terminal.kb",
    "lib.terminal.mul_line", "lib.terminal.exe",
    "bin.ai_tui", "bin.ai_interactive", "bin.ai_lib.ui",
    "bin.ai_lib.tui_deps", "bin.ai_lib.mode",
    "bin.manage", "lib.makecache", "lib.process_control",
    "lib.parse_and_execute", "lib.parse", "lib.safe",
)

import Onyx

# —— 只打桩「交互式密码闸门」，其余初始化逻辑一字不改 ——
Onyx.check_admin_permission = lambda *a, **k: None
Onyx.init_admin_password = lambda *a, **k: True

t0 = time.perf_counter()
try:
    rc = Onyx.run_command_once("echo oneshot-probe")
except SystemExit as e:
    rc = e.code
elapsed = time.perf_counter() - t0

print("\n" + "=" * 60)
print(f"run_command_once 返回: {rc}   总耗时: {elapsed*1000:.1f} ms")
print("=" * 60)
for prefix in WATCH:
    hits = sorted(m for m in sys.modules if m == prefix or m.startswith(prefix + "."))
    if hits:
        print(f"[已加载] {prefix:<28} ({len(hits)} 个模块)")
    else:
        print(f"[未加载] {prefix}")
print("=" * 60)
print("sys.modules 总数:", len(sys.modules))
