# -*- coding: utf-8 -*-
"""
Onyx AI 解析模块 — 纯 Markdown 直通

标记语言已彻底移除：AI 输出就是纯 Markdown 文本，原样累积、原样渲染，
不再解析任何方括号标记（[TXT] / [ANALYSIS] / [ANSWER] / [ASK] / [PLAN] /
[MEMORY] / [PROMPT] / [TAG] / [CLASS] / [SLEEP] / [tool:] / @@SHELL 等）。

工具调用走原生 function calling（流式 delta.tool_calls），无需文本标记；
追问走 choose_ask 工具；暂停走 Sleep 工具；记忆走 MemoryRead/MemorySearch 工具。

返回 dict 保留旧版字段键，供下游（ai_cmd.py / storage.py）空值安全读取。
"""

from typing import Dict, Any


def parse_sse_structured_response(sse_text: str) -> Dict[str, Any]:
    """
    AI 流式输出直通解析：整段文本就是回复本身（纯 Markdown）。

    除 txt 外所有字段返回空默认值，避免破坏下游读取方。
    """
    return {
        "answer": "yes",
        "ask": "",
        "analysis": "",
        "txt": (sse_text or "").strip(),
        "tag": "",
        "memory": "",
        "plan": "",
        "sleep": None,
        "class": "1",
        "prompt": "",
        "tool_calls": [],
    }
