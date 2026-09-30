# -*- coding: utf-8 -*-
"""复现 `./a.sh` 的改写链路：handle_executable_path → resolve_paths_in_multiline_text。"""
import os
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from lib import resolve_path as rp
from lib.parse import handle_executable_path, resolve_paths_in_multiline_text

ROOT_DIR = "/storage/emulated/0/abPython/PythonProject/工具/Hacker--V1.00.1/src/Hacker/onyx-test"
USER_HOME = os.path.join(ROOT_DIR, "home/u0_a305")

print("ROOT_DIR   =", ROOT_DIR)
print("USER_HOME  =", USER_HOME)
print("cmd.py 存在 =", os.path.isfile(os.path.join(ROOT_DIR, "onyx", "cmd.py")))

os.chdir(USER_HOME)          # 模拟用户 cwd = ~（虚拟根内 home）
rp.init_resolve_path(ROOT_DIR, USER_HOME)

print("\ncwd =", os.getcwd())
print("resolve_path('./a.sh') =", rp.resolve_path("./a.sh"))

step1 = handle_executable_path("./a.sh", rp.resolve_path, ROOT_DIR)
print("\n[1] handle_executable_path ->")
print("   ", repr(step1))

step2 = resolve_paths_in_multiline_text(step1, rp.resolve_path)
print("\n[2] replace_virtual_path_in_cmd(resolve_paths_in_multiline_text) ->")
print("   ", repr(step2))

print("\n[3] 逐 token 预览（split(' ')）:")
for t in step2.split(" "):
    print("    ", repr(t))

from lib.parse import resolve_token_path, should_try_path_resolution

print("\n[4] 根因定位 —— resolve_token_path 单独调用:")
for tok in ('./a.sh"', '"source', './a.sh', '"source ./a.sh"'):
    print("    %-16r -> %r" % (tok, resolve_token_path(tok, rp.resolve_path)))
    print("        should_try=%s  strip('\\'\"')=%r" % (
        should_try_path_resolution(tok), tok.strip('\'"')))

print("\n[5] resolve_path 对半截 token 的行为:")
for tok in ('./a.sh"', 'source', '"source'):
    print("    %-12r -> %r" % (tok, rp.resolve_path(tok)))
