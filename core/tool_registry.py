"""core/tool_registry.py — 工具系统已移除，保留空壳兼容旧引用"""

import os
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from core.context import AppContext, ToolInfo


def find_tool(ctx: "AppContext", tool_name: str, request_id: str) -> "Optional[ToolInfo]":
    """工具系统已移除，始终返回 None"""
    return None


def find_similar_tools(ctx: "AppContext", wrong_cmd: str) -> List[Tuple[str, str]]:
    """工具系统已移除"""
    return []


def find_similar_cmds(ctx: "AppContext", wrong_cmd: str) -> List[str]:
    """模糊查找相似系统命令"""
    similar = []
    for cmd in ctx.current_sys_cmds.get(ctx.sys_type, []):
        if wrong_cmd.lower() in cmd.lower():
            similar.append(cmd)
    return similar


def execute_tool(ctx: "AppContext", tool_info: "ToolInfo", args: List[str], request_id: str) -> None:
    """工具系统已移除"""
    pass


def build_tool_index(ctx: "AppContext", request_id: str) -> None:
    """工具系统已移除"""
    pass
