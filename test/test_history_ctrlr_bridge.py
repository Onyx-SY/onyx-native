# -*- coding: utf-8 -*-
"""
Ctrl+R 跨会话反查回归：`OnyxHistory` 把项目历史缓冲桥接给 prompt_toolkit。

背景（2026-09 修复）：此前 `PromptSession` 未传 `history=`，ptk 用的是空的
InMemoryHistory，Ctrl+R 只能搜到「本次进程运行期间」输入的命令，搜不到历史
文件里的旧命令（↑/↓ 走项目自己的 `_HISTORY_BUFFER`，因此不受影响）。

本测试锁定桥接的核心契约：
- `load_history_strings` 必须 oldest-first（ptk 契约），且覆盖 `_HISTORY_BUFFER` 全量
- `store_string` 为 no-op（落盘交给项目既有管线，避免重复写入）
- 空缓冲安全

接线部分（`PromptSession(history=_PTK_HISTORY, search_ignore_case=True)`）由
`test_hist_multiline_e2e.py` 在真实 universal_input 调用路径上断言。
"""
import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib.terminal import input_lib as il


async def _collect(history):
    out = []
    async for item in history.load():
        out.append(item)
    return out


def test_load_is_oldest_first():
    """ptk 要求 load 顺序为 oldest-first；_HISTORY_BUFFER 最新在前 → 必须反转。"""
    il._HISTORY_BUFFER = ["newest", "middle", "oldest"]
    got = asyncio.run(_collect(il.OnyxHistory()))
    assert got == ["oldest", "middle", "newest"], f"load 顺序错误: {got!r}"
    print("  [OK] OnyxHistory.load 顺序 oldest-first，覆盖全量历史")


def test_store_string_is_noop():
    """落盘由 add_to_history / _save_history_buffer_async 负责，桥接层不得重复写。"""
    il._HISTORY_BUFFER = ["a"]
    before = list(il._HISTORY_BUFFER)
    il.OnyxHistory().store_string("brand-new-cmd")
    assert il._HISTORY_BUFFER == before, "store_string 不应改动项目历史缓冲"
    print("  [OK] OnyxHistory.store_string 为 no-op，不重复落盘")


def test_empty_buffer_is_safe():
    il._HISTORY_BUFFER = []
    assert asyncio.run(_collect(il.OnyxHistory())) == []
    print("  [OK] 空历史缓冲安全")


def main():
    test_load_is_oldest_first()
    test_store_string_is_noop()
    test_empty_buffer_is_safe()
    print("ALL CTRL+R BRIDGE TESTS PASSED")


if __name__ == "__main__":
    main()
