# -*- coding: utf-8 -*-
"""Stage4 补丁 B：把 openai 流式 delta 处理抽成可单测的模块级函数。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = os.path.join(ROOT, "bin", "ai_lib", "api.py")

FUNC = '''def _apply_openai_delta(choices, tool_calls_acc, on_content=None, on_tool_call=None):
    """把一个 SSE chunk 的 `choices` 应用到工具调用累积器。

    返回 (正文增量, 思考增量)。抽成独立函数是为了可单测 —— 各家 OpenAI 兼容平台的
    delta 形态差异（delta 为 null / content 是分片数组 / tool_calls 缺 index /
    arguments 直接给 dict）都在这里兜住，而不是散落在长流程里。

    DeepSeek 的报文最规整，只对接 DeepSeek 时这些差异不会暴露。
    """
    if not choices or not isinstance(choices[0], dict):
        return "", ""
    delta = choices[0].get("delta") or {}
    if not isinstance(delta, dict):
        return "", ""
    reasoning = _norm_text_content(delta.get("reasoning_content"))
    content = _norm_text_content(delta.get("content"))
    tc_delta = delta.get("tool_calls")
    if tc_delta and isinstance(tc_delta, list):
        for tc_chunk in tc_delta:
            if not isinstance(tc_chunk, dict):
                continue
            func_delta = tc_chunk.get("function") or {}
            if not isinstance(func_delta, dict):
                func_delta = {}
            tc_idx = tc_chunk.get("index")
            if tc_idx is None:
                # 无 index 的平台：同一位置出现「不同工具名」→ 视为新调用
                tc_idx = 0
                _nm = func_delta.get("name")
                _prev = tool_calls_acc.get(tc_idx) or {}
                _prev_name = (_prev.get("function") or {}).get("name") or ""
                if _nm and _prev_name and _prev_name != _nm:
                    tc_idx = (max(tool_calls_acc) + 1) if tool_calls_acc else 1
            try:
                tc_idx = int(tc_idx)
            except (TypeError, ValueError):
                tc_idx = 0
            if tc_idx not in tool_calls_acc:
                tool_calls_acc[tc_idx] = {"id": "", "type": "function",
                                          "function": {"name": "", "arguments": ""}}
                _tc_name = func_delta.get("name") or ""
                if _tc_name and on_tool_call:
                    on_tool_call(_tc_name)
            tcc = tool_calls_acc[tc_idx]
            if tc_chunk.get("id"):
                tcc["id"] = tc_chunk["id"]
            if tc_chunk.get("type"):
                tcc["type"] = tc_chunk["type"]
            if func_delta.get("name"):
                tcc["function"]["name"] = func_delta["name"]
            if func_delta.get("arguments"):
                # 少数平台参数直接给 dict / list → 归一成 JSON 字符串
                tcc["function"]["arguments"] += _norm_tool_arguments(func_delta["arguments"])
    return content, reasoning


def _parse_sse_openai_responses('''

OLD_LOOP = '''                        # delta 可能是 null（部分平台在最后一帧发 choices:[{delta:null}]）
                        delta = choices[0].get("delta") or {}
                        if not isinstance(delta, dict):
                            continue
                        reasoning = _norm_text_content(delta.get("reasoning_content"))
                        if reasoning:
                            _reasoning_display.append(reasoning)
                            if on_reasoning:
                                on_reasoning(reasoning)
                        # content 形态各家不一（字符串 / 分片数组）→ 统一归一
                        content = _norm_text_content(delta.get("content"))
                        if content:
                            full_content += content
                            if on_content:
                                on_content(content)
                        tc_delta = delta.get("tool_calls")
                        if tc_delta and isinstance(tc_delta, list):
                            for tc_chunk in tc_delta:
                                if not isinstance(tc_chunk, dict):
                                    continue
                                func_delta = tc_chunk.get("function") or {}
                                if not isinstance(func_delta, dict):
                                    func_delta = {}
                                tc_idx = tc_chunk.get("index")
                                if tc_idx is None:
                                    # 无 index 的平台：同一位置出现「不同工具名」→ 视为新调用
                                    tc_idx = 0
                                    _nm = func_delta.get("name")
                                    _prev = _tool_calls_acc.get(tc_idx) or {}
                                    _prev_name = (_prev.get("function") or {}).get("name") or ""
                                    if _nm and _prev_name and _prev_name != _nm:
                                        tc_idx = (max(_tool_calls_acc) + 1) if _tool_calls_acc else 1
                                try:
                                    tc_idx = int(tc_idx)
                                except (TypeError, ValueError):
                                    tc_idx = 0
                                _is_new = tc_idx not in _tool_calls_acc
                                if _is_new:
                                    _tool_calls_acc[tc_idx] = {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                                    _tc_name = func_delta.get("name") or ""
                                    if _tc_name and on_tool_call:
                                        on_tool_call(_tc_name)
                                tcc = _tool_calls_acc[tc_idx]
                                if tc_chunk.get("id"):
                                    tcc["id"] = tc_chunk["id"]
                                if tc_chunk.get("type"):
                                    tcc["type"] = tc_chunk["type"]
                                if func_delta.get("name"):
                                    tcc["function"]["name"] = func_delta["name"]
                                if func_delta.get("arguments"):
                                    # 少数平台参数直接给 dict / list → 归一成 JSON 字符串
                                    tcc["function"]["arguments"] += _norm_tool_arguments(func_delta["arguments"])'''

NEW_LOOP = '''                        # delta 形态差异（null / 分片 content / 缺 index / dict 参数）
                        # 全部在 _apply_openai_delta 里兜住（可单测）
                        _c, _r = _apply_openai_delta(choices, _tool_calls_acc,
                                                     on_content=on_content,
                                                     on_tool_call=on_tool_call)
                        if _r:
                            _reasoning_display.append(_r)
                            if on_reasoning:
                                on_reasoning(_r)
                        if _c:
                            full_content += _c'''


def main():
    with open(API, encoding="utf-8") as f:
        text = f.read()
    for old, new in ((OLD_LOOP, NEW_LOOP), ("def _parse_sse_openai_responses(", FUNC)):
        n = text.count(old)
        if n != 1:
            print(f"FAIL: 命中 {n} 次\n{old[:200]}")
            return 1
        text = text.replace(old, new, 1)
    with open(API, "w", encoding="utf-8") as f:
        f.write(text)
    print("PATCH_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
