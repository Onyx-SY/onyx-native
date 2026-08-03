#!/usr/bin/env python3
"""验证 sado 配置同步修复：模拟启动序列，确认 ctx 与模块全局在最终同步后保持正确。"""
import os
import sys
import tempfile
import shutil

# 使 onyx/onyx 可导入（本文件位于 test/ 下）
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.context import AppContext, init_ctx, get_ctx
from core.bootstrap import init_sado_config as bootstrap_init_sado_config

tmp = tempfile.mkdtemp(prefix="sado_fix_")
try:
    ctx = init_ctx()
    # 模拟 Onyx.py 模块全局（修复前为空，bootstrap 只写 ctx）
    MODULE_SADO_CONFIG_PATH = ""
    MODULE_SADO_CONFIG = []

    ctx.OS_OR_TBS = "TBS"
    ctx.USER_HOME_DIR = tmp          # 模拟 Main.py 设置的虚拟 HOME
    ctx.user_info = {"name": "tester", "is_admin": False, "session_id": "test"}

    # ---- 模拟 Onyx.init_sado_config 包装函数（含本次修复的回写） ----
    def sync_globals_to_ctx():
        global MODULE_SADO_CONFIG_PATH, MODULE_SADO_CONFIG
        ctx.SADO_CONFIG_PATH = MODULE_SADO_CONFIG_PATH
        ctx.SADO_CONFIG = MODULE_SADO_CONFIG

    def init_sado_config_wrapper(request_id):
        global MODULE_SADO_CONFIG_PATH, MODULE_SADO_CONFIG
        sync_globals_to_ctx()
        bootstrap_init_sado_config(ctx, request_id)
        ctx2 = get_ctx()
        MODULE_SADO_CONFIG_PATH = ctx2.SADO_CONFIG_PATH
        MODULE_SADO_CONFIG = ctx2.SADO_CONFIG

    # ---- 启动序列：第 1 次同步（模块全局为空）→ init_sado_config → 第 2 次同步 ----
    sync_globals_to_ctx()
    init_sado_config_wrapper("req-1")
    sync_globals_to_ctx()  # 启动末尾的"再次同步"

    expected = os.path.join(tmp, ".config", "onyx", "sado.json")

    assert os.path.exists(expected), f"配置文件未创建: {expected}"
    assert ctx.SADO_CONFIG_PATH == expected, f"ctx 路径错误: {ctx.SADO_CONFIG_PATH!r}"
    assert MODULE_SADO_CONFIG_PATH == expected, f"模块全局路径错误: {MODULE_SADO_CONFIG_PATH!r}"
    assert len(ctx.SADO_CONFIG) == 1, f"ctx 规则未加载: {ctx.SADO_CONFIG!r}"
    assert len(MODULE_SADO_CONFIG) == 1, f"模块全局规则未加载: {MODULE_SADO_CONFIG!r}"

    # 模拟 sado 命令的检查逻辑（sado_cmd.py:270）
    SADO_CONFIG, SADO_CONFIG_PATH = MODULE_SADO_CONFIG, MODULE_SADO_CONFIG_PATH
    assert SADO_CONFIG and os.path.exists(SADO_CONFIG_PATH), "sado 检查仍误报配置文件不存在！"

    print(f"PASS: 文件已创建于 {expected}")
    print(f"PASS: 最终同步后 ctx.SADO_CONFIG_PATH = {ctx.SADO_CONFIG_PATH}")
    print(f"PASS: 最终同步后模块全局 SADO_CONFIG_PATH = {MODULE_SADO_CONFIG_PATH}")
    print(f"PASS: sado 检查条件通过（规则 {len(SADO_CONFIG)} 条）")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
