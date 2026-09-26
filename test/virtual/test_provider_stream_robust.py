#!/usr/bin/env python3
"""多平台流式解析健壮性回归（非 DeepSeek 平台适配）。

覆盖的真实差异（DeepSeek 报文规整 → 只对接 DeepSeek 时不会暴露）：
  - `data:{...}`（不带空格）→ 旧实现整条流当空响应；
  - `delta` 为 null；
  - `content` 是分片数组 [{"type":"text","text":...}]；
  - `tool_calls` 缺 `index` / 同名续传 / `arguments` 直接给 dict；
  - 平台忽略 `stream:true` → 回整段非流式 JSON（旧实现全空）。

运行: python3 test/virtual/test_provider_stream_robust.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from bin.ai_lib.api import (  # noqa: E402
    _sse_data, _norm_text_content, _norm_tool_arguments,
    _parse_nonstream_openai_json, _apply_openai_delta,
    _plat_cap, _degrade_optional_params,
)


def test_sse_data_tolerant():
    assert _sse_data('data: {"a":1}') == '{"a":1}'
    assert _sse_data('data:{"a":1}') == '{"a":1}', "不带空格的 data: 必须兼容"
    assert _sse_data("data:   [DONE]") == "[DONE]"
    assert _sse_data("") is None
    assert _sse_data("event: message_start") is None, "非 data 行应跳过"
    assert _sse_data(": keep-alive") is None
    print("PASS SSE 行解析：data: / data:（无空格）/ [DONE] / 非数据行")


def test_norm_text_content():
    assert _norm_text_content("abc") == "abc"
    assert _norm_text_content(None) == ""
    assert _norm_text_content([{"type": "text", "text": "甲"}, {"text": "乙"}]) == "甲乙"
    assert _norm_text_content({"text": "丙"}) == "丙"
    assert _norm_text_content(["a", "b"]) == "ab"
    print("PASS content 归一：字符串 / 分片数组 / dict / None")


def test_norm_tool_arguments():
    assert _norm_tool_arguments('{"a":1}') == '{"a":1}'
    assert json.loads(_norm_tool_arguments({"a": 1})) == {"a": 1}
    assert _norm_tool_arguments(None) == ""
    print("PASS 工具参数归一：字符串 / dict / None")


def test_nonstream_fallback():
    body = json.dumps({
        "choices": [{"message": {
            "content": "非流式回复",
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": "RunCommand", "arguments": {"cmd": "ls"}}}],
        }}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 5},
    })
    acc = {}
    got = []
    names = []
    content, usage = _parse_nonstream_openai_json(
        body, on_content=got.append, on_tool_call=names.append, tool_calls_acc=acc)
    assert content == "非流式回复", content
    assert got == ["非流式回复"]
    assert names == ["RunCommand"], names
    assert json.loads(acc[0]["function"]["arguments"]) == {"cmd": "ls"}, acc
    assert usage.get("completion_tokens") == 5
    print("PASS 非流式兜底：整段 JSON → 内容 / 工具调用 / usage")

    # 不是 JSON（普通 SSE 残留）→ 安静返回空
    assert _parse_nonstream_openai_json("not json") == ("", {})
    print("PASS 非流式兜底：非 JSON 输入安全返回空")


def test_delta_null_and_shapes():
    acc = {}
    # delta 为 null（部分平台最后一帧）
    assert _apply_openai_delta([{"delta": None}], acc) == ("", "")
    # 空 choices / 非法结构
    assert _apply_openai_delta([], acc) == ("", "")
    assert _apply_openai_delta(None, acc) == ("", "")
    assert _apply_openai_delta(["x"], acc) == ("", "")
    print("PASS delta 容错：null / 空 choices / 非法结构均安全")

    # 分片 content + reasoning
    c, r = _apply_openai_delta(
        [{"delta": {"content": [{"type": "text", "text": "你"}], "reasoning_content": [{"text": "想"}]}}], acc)
    assert c == "你" and r == "想", (c, r)
    print("PASS 分片数组形态的 content / reasoning 被正确归一")


def test_tool_calls_without_index():
    acc = {}
    names = []
    # 平台 A：两个不同工具都不带 index → 必须识别为两次独立调用
    _apply_openai_delta([{"delta": {"tool_calls": [
        {"function": {"name": "Read", "arguments": '{"p":'}}]}}], acc, on_tool_call=names.append)
    _apply_openai_delta([{"delta": {"tool_calls": [
        {"function": {"arguments": '"a.txt"}'}}]}}], acc)
    _apply_openai_delta([{"delta": {"tool_calls": [
        {"function": {"name": "Write", "arguments": {"p": "b", "v": 1}}}]}}], acc, on_tool_call=names.append)
    assert names == ["Read", "Write"], names
    assert acc[0]["function"]["name"] == "Read"
    assert acc[0]["function"]["arguments"] == '{"p":"a.txt"}', acc[0]
    assert json.loads(acc[1]["function"]["arguments"]) == {"p": "b", "v": 1}, acc[1]
    print(f"PASS 无 index 的并行工具调用被正确拆分：{names}")

    # 平台 B：带 index 的常规增量
    acc2 = {}
    for frag in ['{"a"', ":1}"]:
        _apply_openai_delta([{"delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "function": {"name": "RunCommand", "arguments": frag}}]}}], acc2)
    assert acc2[0]["id"] == "call_1"
    assert acc2[0]["function"]["arguments"] == '{"a":1}', acc2
    print("PASS 带 index 的常规增量拼接正确")

    # index 为字符串 / None
    acc3 = {}
    _apply_openai_delta([{"delta": {"tool_calls": [
        {"index": "0", "function": {"name": "X", "arguments": "{}"}}]}}], acc3)
    _apply_openai_delta([{"delta": {"tool_calls": [
        {"index": None, "function": {"arguments": ""}}]}}], acc3)
    assert 0 in acc3 and acc3[0]["function"]["name"] == "X", acc3
    print("PASS index 为字符串 / None 时不会崩且归并到同一调用")


def test_platform_capabilities():
    """可选字段按平台能力发送（key.json 的 capabilities 可覆盖）。"""
    assert _plat_cap({}, "stream_options", True) is True, "默认应保持现状"
    assert _plat_cap({"capabilities": {"stream_options": False}}, "stream_options", True) is False
    assert _plat_cap({"capabilities": {"thinking": False}}, "thinking", True) is False
    assert _plat_cap({"capabilities": {}}, "thinking", True) is True
    assert _plat_cap(None, "thinking", True) is True, "空配置不应崩"
    print("PASS 平台能力开关：默认放行，capabilities 可关")


def test_auto_degrade_optional_params():
    """平台报「未知参数」→ 摘掉可选字段重试；不相关报错不动 payload。"""
    payload = {"model": "x", "thinking": {"type": "enabled"},
               "reasoning_effort": "high", "stream_options": {"include_usage": True},
               "messages": []}
    assert _degrade_optional_params(payload, "Unrecognized request argument supplied: thinking") is True
    assert "thinking" not in payload and "reasoning_effort" not in payload
    assert "stream_options" not in payload
    assert payload["model"] == "x" and "messages" in payload, "非可选字段必须保留"
    print("PASS 自动降级：未知参数报错 → 摘掉 thinking/reasoning_effort/stream_options")

    p2 = {"model": "x"}
    assert _degrade_optional_params(p2, "Unrecognized request argument") is False, "无可摘字段应返回 False"
    p3 = {"thinking": 1}
    assert _degrade_optional_params(p3, "model not found") is False, "不相关报错不应动 payload"
    assert "thinking" in p3
    assert _degrade_optional_params(p3, "未知参数：thinking") is True, "中文报错也要认"
    print("PASS 自动降级：仅在「未知/不支持参数」类报错时触发")


def test_source_uses_tolerant_parsing():
    src = open(os.path.join(ROOT, "bin", "ai_lib", "api.py"), encoding="utf-8").read()
    assert 'startswith("data: ")' not in src, "不应再只认带空格的 data: 写法"
    assert src.count("_sse_data(line)") >= 4, "四处 SSE 解析都应走容错解析"
    assert "_parse_nonstream_openai_json" in src, "缺少非流式兜底"
    print("PASS 源码断言：SSE 容错 + 非流式兜底已接入")


def main():
    test_sse_data_tolerant()
    test_norm_text_content()
    test_norm_tool_arguments()
    test_nonstream_fallback()
    test_delta_null_and_shapes()
    test_tool_calls_without_index()
    test_platform_capabilities()
    test_auto_degrade_optional_params()
    test_source_uses_tolerant_parsing()
    print("\nALL PASS")


if __name__ == "__main__":
    main()
