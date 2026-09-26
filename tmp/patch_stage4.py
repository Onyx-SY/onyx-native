# -*- coding: utf-8 -*-
"""Stage4 补丁：非 DeepSeek 平台适配加固（SSE 容错 / content 归一 / 工具调用容错 / 非流式兜底）。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = os.path.join(ROOT, "bin", "ai_lib", "api.py")

PATCHES = []


def patch(old, new):
    PATCHES.append((old, new))


HELPERS = '''def _sse_data(line):
    """取 SSE 行的 data 段，兼容 `data: {...}` 与 `data:{...}` 两种写法。

    OpenAI / DeepSeek 用带空格的写法，但部分兼容平台（自建网关、部分国产平台）
    不带空格。旧实现只认 `data: ` → 整条流被当成「空响应」，表现为「AI 不说话」。
    """
    if not line:
        return None
    s = line if isinstance(line, str) else str(line)
    s = s.strip()
    if not s.startswith("data:"):
        return None
    return s[5:].lstrip()


def _norm_text_content(value) -> str:
    """把各家平台的 `content` / `reasoning_content` 形态归一成字符串。

    实测差异：字符串 / [{"type":"text","text":...}] / [{"text":...}] / {"text":...} 都有。
    DeepSeek 只发字符串，所以只对接 DeepSeek 时这个差异不会暴露，换平台就崩
    （`full_content += content` 对 list 直接 TypeError）。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for k in ("text", "content", "value"):
            v = value.get(k)
            if isinstance(v, str):
                return v
        return ""
    if isinstance(value, (list, tuple)):
        return "".join(_norm_text_content(v) for v in value)
    return str(value)


def _norm_tool_arguments(value) -> str:
    """工具调用参数归一成 JSON 字符串（部分平台直接给 dict / list）。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _parse_nonstream_openai_json(text, on_content=None, on_tool_call=None, tool_calls_acc=None):
    """把**非流式** OpenAI 响应体解析成 (content, usage)。

    兜底场景：部分兼容平台忽略 `stream: true`，直接回一整段 JSON（没有 `data:` 行）。
    旧实现会跳过所有行 → 内容与工具调用全空。
    """
    try:
        data = json.loads(text)
    except Exception:
        return "", {}
    if not isinstance(data, dict):
        return "", {}
    choices = data.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return "", data.get("usage") or {}
    msg = choices[0].get("message") or {}
    if not isinstance(msg, dict):
        return "", data.get("usage") or {}
    content = _norm_text_content(msg.get("content"))
    if content and on_content:
        on_content(content)
    for i, tc in enumerate(msg.get("tool_calls") or []):
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        if not isinstance(fn, dict):
            fn = {}
        name = fn.get("name") or ""
        args = _norm_tool_arguments(fn.get("arguments"))
        if tool_calls_acc is not None:
            tool_calls_acc[i] = {
                "id": tc.get("id") or "",
                "type": tc.get("type") or "function",
                "function": {"name": name, "arguments": args},
            }
        if name and on_tool_call:
            on_tool_call(name)
    return content, data.get("usage") or {}


def _parse_sse_openai_responses(lines, on_content=None, on_tool_call=None, should_stop=None):'''

patch("def _parse_sse_openai_responses(lines, on_content=None, on_tool_call=None, should_stop=None):", HELPERS)

# ── 四处 SSE 行解析统一为容错版 ──
patch(
    """        if not line or not line.startswith("data: "):
            continue
        data_str = line[6:]
        try:
            chunk = json.loads(data_str)
        except json.JSONDecodeError:
            continue
        if not isinstance(chunk, dict):
            continue
        etype = chunk.get("type", "")""",
    """        data_str = _sse_data(line)
        if not data_str:
            continue
        try:
            chunk = json.loads(data_str)
        except json.JSONDecodeError:
            continue
        if not isinstance(chunk, dict):
            continue
        etype = chunk.get("type", "")""",
)

patch(
    """        if not line or not line.startswith("data: "):
            continue
        data_str = line[6:]
        try:
            chunk = json.loads(data_str)
        except json.JSONDecodeError:
            continue
        if not isinstance(chunk, dict):
            continue
        um = chunk.get("usageMetadata") or {}""",
    """        data_str = _sse_data(line)
        if not data_str:
            continue
        try:
            chunk = json.loads(data_str)
        except json.JSONDecodeError:
            continue
        if not isinstance(chunk, dict):
            continue
        um = chunk.get("usageMetadata") or {}""",
)

patch(
    """                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":""",
    """                    data_str = _sse_data(line)
                    if data_str is None:
                        # 不是 SSE 数据行：可能是「平台忽略 stream 参数」的整段 JSON
                        if line and line.strip():
                            _raw_lines.append(line)
                        continue
                    if data_str.strip() == "[DONE]":""",
)

patch(
    """                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    try:
                        chunk = json.loads(data_str)
                        if not isinstance(chunk, dict):
                            continue
                        ctype = chunk.get("type", "")""",
    """                    data_str = _sse_data(line)
                    if not data_str:
                        continue
                    try:
                        chunk = json.loads(data_str)
                        if not isinstance(chunk, dict):
                            continue
                        ctype = chunk.get("type", "")""",
)

# ── openai 分支：raw 行缓冲 + delta/工具调用容错 ──
patch(
    """            if stream_fmt == "openai":
                for line in response.iter_lines(decode_unicode=True):""",
    """            if stream_fmt == "openai":
                # 非 SSE 行缓冲：兜住「平台忽略 stream:true、直接回整段 JSON」的情况
                _raw_lines: List[str] = []
                for line in response.iter_lines(decode_unicode=True):""",
)

patch(
    """                        delta = choices[0].get("delta", {})
                        if not isinstance(delta, dict):
                            continue
                        reasoning = delta.get("reasoning_content")
                        if reasoning:
                            _reasoning_display.append(reasoning)
                            if on_reasoning:
                                on_reasoning(reasoning)
                        content = delta.get("content")
                        if content:
                            full_content += content
                            if on_content:
                                on_content(content)
                        tc_delta = delta.get("tool_calls")
                        if tc_delta and isinstance(tc_delta, list):
                            for tc_chunk in tc_delta:
                                if not isinstance(tc_chunk, dict):
                                    continue
                                tc_idx = tc_chunk.get("index", 0)
                                _is_new = tc_idx not in _tool_calls_acc
                                if _is_new:
                                    _tool_calls_acc[tc_idx] = {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                                    _tc_name = tc_chunk.get("function", {}).get("name", "")
                                    if _tc_name and on_tool_call:
                                        on_tool_call(_tc_name)
                                tcc = _tool_calls_acc[tc_idx]
                                if tc_chunk.get("id"):
                                    tcc["id"] = tc_chunk["id"]
                                if tc_chunk.get("type"):
                                    tcc["type"] = tc_chunk["type"]
                                func_delta = tc_chunk.get("function", {})
                                if func_delta.get("name"):
                                    tcc["function"]["name"] = func_delta["name"]
                                if func_delta.get("arguments"):
                                    tcc["function"]["arguments"] += func_delta["arguments"]""",
    """                        # delta 可能是 null（部分平台在最后一帧发 choices:[{delta:null}]）
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
                                    tcc["function"]["arguments"] += _norm_tool_arguments(func_delta["arguments"])""",
)

# ── openai 分支收尾：非流式兜底 ──
patch(
    """                    except json.JSONDecodeError:
                        continue
            elif stream_fmt == "openai_responses":""",
    """                    except json.JSONDecodeError:
                        continue
                # ── 非流式兜底：平台忽略 stream:true → 整段 JSON，没有 data: 行 ──
                if not full_content and not _tool_calls_acc and _raw_lines:
                    _c, _u = _parse_nonstream_openai_json("".join(_raw_lines), on_content, on_tool_call, _tool_calls_acc)
                    if _c:
                        full_content = _c
                    if _u:
                        _usage = _u
            elif stream_fmt == "openai_responses":""",
)


def main():
    with open(API, encoding="utf-8") as f:
        text = f.read()
    for old, new in PATCHES:
        n = text.count(old)
        if n != 1:
            print(f"FAIL: 命中 {n} 次（期望 1）")
            print(old[:300])
            return 1
        text = text.replace(old, new, 1)
        print(f"OK   {len(old)}B → {len(new)}B")
    with open(API, "w", encoding="utf-8") as f:
        f.write(text)
    print("PATCH_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
