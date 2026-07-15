"""
lib/native_fs/ — Onyx 自研文件编辑系统

替代 MCP filesystem 作为 AI 文件操作的主力。
提供标记语言解析、文件操作引擎、彩色反馈面板。

使用入口：
    from lib.native_fs import process_markup
    results = process_markup(ai_response_text, cwd="/project")

返回: List[BlockResult]
    BlockResult = {
        "type": str,       # view/edit/write/append/insert/delete
        "success": bool,
        "path": str,
        "message": str,
        "content": str|None,  # VIEW 的读取内容
    }
"""

from .markup_parser import parse_markup
from .engine import execute_block, BlockResult
from .panels import PanelManager

# 全局面板管理器（单例）
panel_manager = PanelManager()


def process_markup(text: str, cwd: str = None) -> list:
    """
    解析并执行 AI 回复文本中的全部标记块。

    参数:
        text: AI 回复文本（可能包含 [VIEW:]、[EDIT:] 等标记）
        cwd:  工作目录，用于解析相对路径

    返回:
        [BlockResult, ...] — 每个标记块的执行结果
    """
    blocks = parse_markup(text)
    results = []

    for block in blocks:
        panel_manager.clear_previous()
        result = execute_block(block, cwd, panel_manager)
        results.append(result)

    return results


def process_blocks(blocks: list, cwd: str = None) -> list:
    """
    直接执行已解析的标记块列表（跳过解析步骤）。

    用于 ai_cmd.py 中已分离出 blocks 的场景。
    """
    results = []
    for block in blocks:
        panel_manager.clear_previous()
        result = execute_block(block, cwd, panel_manager)
        results.append(result)
    return results
