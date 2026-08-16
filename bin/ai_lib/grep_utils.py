# -*- coding: utf-8 -*-
"""
grep_utils.py — 文件搜索核心逻辑（grep -rn）

MemorySearch 与 grep_search 共用此逻辑；独立成模块以避免 ai_cmd ↔ memory_tools 循环依赖。
"""
import subprocess
from typing import List, Optional


def _run_grep_lines(pattern: str, search_paths, context: int = 0,
                    case_insensitive: bool = False, glob: str = None,
                    timeout: int = 15) -> Optional[str]:
    """grep -rn 核心文件搜索逻辑：返回含 file:line:content 的原始匹配文本。

    超时返回 None；无匹配返回空字符串。MemorySearch 与 grep_search 共用此逻辑。
    """
    cmd = ["grep", "-rn", "-H"]  # -H：单文件搜索时也输出文件名（MemorySearch 分组依赖）
    if case_insensitive:
        cmd.append("-i")
    if context and context > 0:
        cmd.append(f"-C{context}")
    if glob:
        cmd.extend(["--include", glob])
    cmd.append("--")
    cmd.append(pattern)
    if isinstance(search_paths, str):
        search_paths = [search_paths]
    cmd.extend(search_paths)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode == 0 or result.returncode == 1:
            return result.stdout
        return result.stderr.strip() or None
    except subprocess.TimeoutExpired:
        return None
