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


# 标记块类型：哪些是修改操作
_MUTATION_TYPES = frozenset({
    "edit", "edit_range", "write", "delete", "delete_by_content",
    "append", "insert", "replace_all",
})


def _count_mutations(blocks: list) -> int:
    """统计列表中的修改操作数量"""
    return sum(1 for b in blocks if b.get("type") in _MUTATION_TYPES)


def process_markup(text: str, cwd: str = None) -> list:
    """
    解析并执行 AI 回复文本中的全部标记块。

    铁律：每次只执行一个修改操作。多个 [EDIT:]/[WRITE:] 块只执行第一个。

    参数:
        text: AI 回复文本（可能包含 [VIEW:]、[EDIT:] 等标记）
        cwd:  工作目录，用于解析相对路径

    返回:
        [BlockResult, ...] — 每个标记块的执行结果
    """
    blocks = parse_markup(text)
    return _process_blocks_with_limit(blocks, cwd)


def process_blocks(blocks: list, cwd: str = None) -> list:
    """
    直接执行已解析的标记块列表（跳过解析步骤）。

    用于 ai_cmd.py 中已分离出 blocks 的场景。
    同样遵守单修改块铁律。
    """
    return _process_blocks_with_limit(blocks, cwd)


def _process_blocks_with_limit(blocks: list, cwd: str = None) -> list:
    """
    内部：执行标记块，但限制修改操作每次只能有一个。

    - VIEW 块不受限（只读）
    - 多个修改块时，只执行第一个，其余返回跳过警告
    """
    total_mutations = _count_mutations(blocks)
    mutation_seen = False
    results = []

    for block in blocks:
        panel_manager.clear_previous()
        is_mutation = block.get("type") in _MUTATION_TYPES

        if is_mutation and mutation_seen:
            # 已有修改块执行过 → 这个跳过
            path = block.get("path", "")
            results.append(BlockResult(
                block, False,
                f"⚠️ 检测到多个编辑块，已跳过（铁律：每次只做一个编辑）。"
                f"请等上一个 [:{block['type']}] 执行完成后再输出下一个。",
            ))
            continue

        if is_mutation and total_mutations > 1:
            mutation_seen = True

        result = execute_block(block, cwd, panel_manager)
        results.append(result)

    return results
