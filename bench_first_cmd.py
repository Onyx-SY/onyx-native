#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""基准测试：模拟首次命令执行的耗时分布（临时脚本，测完删除）"""
import sys, os, time, json

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

def t(label, fn):
    s = time.perf_counter()
    r = fn()
    ms = (time.perf_counter() - s) * 1000
    print(f"  {label:<52} {ms:>9.2f} ms")
    return r

print(f"Python {sys.version.split()[0]}  cwd={os.getcwd()}")

# 1) 首次 import（含 parse / safe / terminal.exe / makecache 等依赖链）
t("import lib.parse_and_execute", lambda: __import__("lib.parse_and_execute"))

from lib.parse_and_execute import (
    _get_global_cache, _load_cmd_mapping_cache, _determine_cmd_type_local,
    check_advanced_syntax, _LANG_MESSAGES,
)
from lib.parse import smart_shlex_split
from lib.safe import (
    load_perm_path_config, load_other_terminal_commands, check_dangerous_commands,
    check_variable_command_in_fine_grained_path,
)
from lib.get_terminal_type import get_terminal_type

ROOT = os.path.abspath(".")

# 2) 首次 JSON 配置加载
t("load_perm_path_config (perm_path.json 6.8KB)", lambda: load_perm_path_config(ROOT, "test", None))
t("load_other_terminal_commands (other_terminal_cmd.json 3.9KB)", lambda: load_other_terminal_commands(ROOT))

# 3) msgpack 命令映射（模拟缓存文件不存在 → 回退路径；再模拟 1 万条命令的内存映射）
t("_load_cmd_mapping_cache (msgpack, 无文件)", lambda: _load_cmd_mapping_cache("bash"))
big_mapping = {"bash": {"mapping": {"system": [f"cmd{i:05d}" for i in range(10000)], "tools": {}}}}
t("_get_global_cache (内存缓存 1 万命令)", lambda: _get_global_cache("bash", CMD_MAPPING_CACHE=big_mapping))

# 4) 每条命令都会走的检查
def dangerous():
    for _ in range(100):
        check_dangerous_commands("echo hello", None)
t("check_dangerous_commands x100 (dan_cmd 每次读盘)", dangerous)

def fine_grained():
    for _ in range(100):
        check_variable_command_in_fine_grained_path("ls -la", os.getcwd(), ROOT, "test")
t("check_variable_command_in_fine_grained_path x100", fine_grained)

def parse_cmd():
    for _ in range(100):
        smart_shlex_split("ls -la /tmp", "bash")
        check_advanced_syntax("ls -la /tmp", {}, "bash")
t("smart_shlex_split + check_advanced_syntax x100", parse_cmd)

t("get_terminal_type()", get_terminal_type)

# 5) 完整 parse_and_execute 首次调用（无 shell 依赖的静态部分）
import lib.parse_and_execute as PAE
from types import SimpleNamespace

def full_once():
    PAE.parse_and_execute(
        "echo hello", is_recursive=False, is_ai_triggered=False,
        BUILTIN_COMMANDS={}, CMD_MAPPING_CACHE={}, sys_type="bash",
        user_mode=None, global_config=None, executor=None,
        PROCESS_LOCK=None, CURRENT_PROCESSES=None, AI_TOOL_OUTPUT_CACHE=None,
        USER_HOME_DIR=None, ROOT_DIR=ROOT, TOOL_MAIN_DIR=None, PYTHON_EXE=sys.executable,
        executable_config=None, SANDBOX_CONFIG=None,
        DEBUG_PARSECMD_PATH=None, DEBUG_TIMES_PATH=None,
        PATH_INDEX_MSG_PATH=None, DIR_CACHE_MSG_PATH=None, CMD_MAPPING_MSG_PATH=None,
        OTHER_TERMINAL_CMDS=None,
        get_current_lang_func=lambda: "chinese",
        resolve_path_func=None, check_sandbox_path_func=None,
        validate_param_path_func=None, get_cached_cmd_func=None,
        check_tool_permission_func=None, find_similar_tools_func=None,
        find_similar_cmds_func=None, run_cmd_sync_func=None,
        run_cmd_with_redirect_func=None, execute_tool_func=None,
        replace_virtual_path_in_cmd_func=None, get_virtual_path_func=None,
        check_blocked_cmd_func=None, is_interactive_command_func=None,
        read_config_file_func=None, clear_ai_cmd_cache_func=None,
        build_tool_index_func=None, load_cmd_mapping_cache_func=None,
        log_info_func=None, log_error_func=None, log_warning_func=None,
        security_log_func=None, username="test",
    )
t("parse_and_execute('echo hello') 完整首次调用", full_once)

print("done")
