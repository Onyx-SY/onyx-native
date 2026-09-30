# -*- coding: utf-8 -*-
"""验证：挂起的直接原因是双引号不闭合（引号被 '.'' 替换掉）。"""
import os
import subprocess
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from lib import resolve_path as rp
from lib.parse import handle_executable_path, resolve_paths_in_multiline_text

ROOT_DIR = "/storage/emulated/0/abPython/PythonProject/工具/Hacker--V1.00.1/src/Hacker/onyx-test"
USER_HOME = os.path.join(ROOT_DIR, "home/u0_a305")
os.chdir(USER_HOME)
rp.init_resolve_path(ROOT_DIR, USER_HOME)

raw = handle_executable_path("./a.sh", rp.resolve_path, ROOT_DIR)      # 改写产物
final = resolve_paths_in_multiline_text(raw, rp.resolve_path)          # 送 PTY 前的最终串

def report(tag, s):
    q = s.count('"')
    r = subprocess.run(["bash", "-n", "-c", s], capture_output=True, text=True)
    print(f"--- {tag} ---")
    print("  串:", repr(s))
    print("  双引号个数:", q, "(偶数=闭合 / 奇数=不闭合)")
    print("  bash -n 退出码:", r.returncode, "| stderr:", r.stderr.strip() or "(无)")
    print()

report("① 改写产物（正确，引号成对）", raw)
report("② 路径替换后（最终送 PTY 的串）", final)

# 反证：把末尾被吃掉的引号补回去，语法立刻恢复
fixed = final[:-1] + '"'
report("③ 反证：仅把末尾 '.' 换回 '\"'", fixed)

# 对照：单独看那个被撕开的 token
print("--- ④ 元凶 token ---")
tok_before = './a.sh"'
from lib.parse import resolve_token_path
tok_after = resolve_token_path(tok_before, rp.resolve_path)
print("  替换前:", repr(tok_before), " 引号个数:", tok_before.count('"'))
print("  替换后:", repr(tok_after), " 引号个数:", tok_after.count('"'))
print("  首字符:", repr(tok_before[0]), "→ 被当成引号字符，同时贴到首尾")
