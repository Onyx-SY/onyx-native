#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
source 命令处理器 — 统一走 Onyx 解析执行

脚本内容逐条交给 parse_and_execute：内置命令（clear/cd/manage 等）在 Python 层执行，
系统命令由 Onyx 再交给底层 shell，保证虚拟路径/沙箱/权限链路完整。
支持多行结构合并（if/for/while/case/heredoc/续行）。
"""

import os
import sys
from typing import List


def handle_source(cmd_parts: List[str], request_id: str) -> None:
    """延迟导入版：首次调用时才 import Onyx 重依赖，避免启动开销"""
    if len(cmd_parts) < 2:
        return

    script_path = _resolve_script_path(cmd_parts[1], request_id)
    if not script_path:
        return

    # 统一走 Onyx 脚本解析：脚本内每条命令都交给 parse_and_execute，
    # 由 Onyx 识别内置命令（clear/cd/manage…）并在 Python 层执行，
    # 系统命令再由 Onyx 交给底层 shell。
    # 不再直接把 `source <file>` 丢给底层 shell——那会让脚本内的
    # clear/cd 等绕过 Onyx 检查，导致虚拟路径/沙箱链路断裂。
    _source_onyx_script(script_path, request_id)


def _resolve_script_path(path: str, request_id: str) -> str:
    """解析脚本路径并做沙盒检查"""
    from Onyx import resolve_path, check_sandbox_path, Fore, Style, global_config

    current_lang = global_config["display_info"]["language"]["current"]
    script_path = resolve_path(path)

    if not os.path.exists(script_path):
        print(Fore.RED + {
            "chinese": f"文件不存在: {script_path}",
            "english": f"File not found: {script_path}"
        }[current_lang] + Style.RESET_ALL)
        return ""

    if not os.path.isfile(script_path):
        return ""

    if not check_sandbox_path(script_path, request_id):
        return ""

    return script_path


# ═══════════════════════════════════════════════════════════════════
# Onyx 脚本 — 逐行解析 + 多行结构合并，逐条交给 Onyx 执行
# ═══════════════════════════════════════════════════════════════════
def _source_onyx_script(script_path: str, request_id: str) -> None:
    """
    纯逐行读取 source 执行 Onyx 脚本
    自动识别：if/for/while/until/case/here-doc <<EOF / 续行 \
    把完整结构一次性发给 parse_and_execute，保证语法正确
    """
    from Onyx import parse_and_execute, Fore, Style, global_config

    current_lang = global_config["display_info"]["language"]["current"]
    lang_msgs = {
        "chinese": {
            "shebang_detected": "检测到 shebang，将作为 Onyx 脚本执行",
            "exec_failed": "执行失败: {}",
            "script_failed": "脚本执行失败: {}",
            "empty_file": "文件为空"
        },
        "english": {
            "shebang_detected": "Shebang detected, will execute as Onyx script",
            "exec_failed": "Execution failed: {}",
            "script_failed": "Script execution failed: {}",
            "empty_file": "File is empty"
        }
    }
    msg = lang_msgs[current_lang]

    try:
        with open(script_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()

        if not lines:
            print(Fore.YELLOW + msg["empty_file"] + Style.RESET_ALL)
            return

        # ==============================================
        # 核心：逐行解析 + 多行结构合并（纯逐行读取）
        # ==============================================
        block_buffer = []          # 多行命令块
        in_continuation = False     # 续行 \
        in_here_doc = False         # here-doc <<EOF
        here_doc_end = ""           # here-doc 结束符
        struct_stack = []           # 结构栈 if/for/while/case

        # 关键字判断
        start_keywords = {"if", "for", "while", "until", "case"}
        mid_keywords = {"then", "do", "in"}
        end_keywords = {"fi", "done", "esac"}
        ignore_prefix = {"#", ";", ""}

        for raw_line in lines:
            line = raw_line.rstrip('\n')
            stripped = line.strip()

            # 空行 / 注释行
            if not in_continuation and not in_here_doc:
                if stripped.startswith("#") or stripped in ignore_prefix:
                    continue
                if stripped.startswith("!#"):
                    continue

            # ============= HERE-DOCUMENT <<EOF =============
            if not in_here_doc and not in_continuation:
                if "<<<" in stripped:
                    pass
                elif "<<" in stripped:
                    parts = stripped.split("<<", 1)
                    left = parts[0].strip()
                    right = parts[1].strip()
                    if right.startswith("-"):
                        right = right[1:].strip()
                    if right and not right.startswith(">"):
                        # 进入 here-doc
                        in_here_doc = True
                        here_doc_end = right.split()[0]
                        block_buffer.append(line)
                        continue

            if in_here_doc:
                block_buffer.append(line)
                if stripped == here_doc_end:
                    in_here_doc = False
                    here_doc_end = ""
                continue

            # ============= 续行 \ =============
            if stripped.endswith("\\"):
                block_buffer.append(line.rstrip('\\'))
                in_continuation = True
                continue
            if in_continuation:
                block_buffer.append(line)
                in_continuation = False
                if not stripped:
                    continue

            # ============= 结构化命令 if/for/while/case =============
            first_word = stripped.split()[0] if stripped else ""

            if first_word in start_keywords:
                struct_stack.append(first_word)
                block_buffer.append(line)
            elif first_word in mid_keywords:
                block_buffer.append(line)
            elif first_word in end_keywords:
                if struct_stack:
                    struct_stack.pop()
                block_buffer.append(line)
                # 结构结束 → 提交整块
                if not struct_stack:
                    full_cmd = "\n".join(block_buffer)
                    parse_and_execute(full_cmd, is_recursive=True)
                    block_buffer = []
            else:
                # 普通行
                block_buffer.append(line)
                # 无结构栈 → 立即提交
                if not struct_stack:
                    full_cmd = "\n".join(block_buffer)
                    parse_and_execute(full_cmd, is_recursive=True)
                    block_buffer = []

        # 最后剩余块
        if block_buffer:
            full_cmd = "\n".join(block_buffer)
            parse_and_execute(full_cmd, is_recursive=True)

    except Exception as e:
        print(Fore.RED + msg["script_failed"].format(str(e)) + Style.RESET_ALL)
