# -*- coding: utf-8 -*-
"""Stage6 补丁 D：补测试。
  1) 真实按键路径（pilot.press）连按 ↑ 能逐条翻历史（覆盖异步 Changed 复位 bug）
  2) 平台能力开关 + 400 自动降级
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEST = os.path.join(ROOT, "test", "virtual", "test_tui_arrow_gesture.py")
PROV = os.path.join(ROOT, "test", "virtual", "test_provider_stream_robust.py")

P = [
    # ── 1) 真实按键路径 ──
    (
        GEST,
        """        print(f"PASS ↓ 滑动流同样被识别（日志滚动 {before}→{log.scroll_y}）")""",
        """        print(f"PASS ↓ 滑动流同样被识别（日志滚动 {before}→{log.scroll_y}）")

        # ── 5) 真实按键路径（走 Textual 事件循环 + 异步 Changed）──
        # 这一条专门盯「程序化写入历史 → 异步 Input.Changed 把游标复位」的 bug：
        # 修好之前，连按 ↑ 会永远停在最新一条（每次都被复位回 len(_hist)）。
        app._hist = ["第一条", "第二条", "第三条"]
        app._hist_idx = len(app._hist)
        app._arrow_t = 0.0
        app._arrow_gesture = False
        app._hist_snap = None
        app._hist_prog_values = []
        inp.value = ""
        await pilot.pause()
        seen = []
        for _ in range(3):
            await pilot.press("up")
            await pilot.pause()
            await pilot.pause()
            seen.append(inp.value)
        assert seen == ["第一条", "第二条", "第三条"], f"真实按键翻历史异常：{seen}"
        print(f"PASS 真实按键路径（含异步 Changed）逐条翻历史：{seen}")

        # 下翻回草稿
        for _ in range(4):
            await pilot.press("down")
            await pilot.pause()
        assert app._hist_idx == len(app._hist), f"↓ 到底应恢复「未翻历史」状态，实际 {app._hist_idx}"
        print("PASS 真实按键 ↓ 到底恢复草稿状态")""",
    ),
    # ── 2) 平台能力开关 + 自动降级 ──
    (
        PROV,
        """from bin.ai_lib.api import (  # noqa: E402
    _sse_data, _norm_text_content, _norm_tool_arguments,
    _parse_nonstream_openai_json, _apply_openai_delta,
)""",
        """from bin.ai_lib.api import (  # noqa: E402
    _sse_data, _norm_text_content, _norm_tool_arguments,
    _parse_nonstream_openai_json, _apply_openai_delta,
    _plat_cap, _degrade_optional_params,
)""",
    ),
    (
        PROV,
        """def test_source_uses_tolerant_parsing():""",
        '''def test_platform_capabilities():
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


def test_source_uses_tolerant_parsing():''',
    ),
    (
        PROV,
        """    test_source_uses_tolerant_parsing()
    print("\\nALL PASS")""",
        """    test_platform_capabilities()
    test_auto_degrade_optional_params()
    test_source_uses_tolerant_parsing()
    print("\\nALL PASS")""",
    ),
]


def main():
    cache = {}
    for path, old, new in P:
        if path not in cache:
            with open(path, encoding="utf-8") as f:
                cache[path] = f.read()
        text = cache[path]
        n = text.count(old)
        if n != 1:
            print(f"FAIL {os.path.basename(path)}: 命中 {n} 次\n{old[:200]}")
            return 1
        cache[path] = text.replace(old, new, 1)
    for path, text in cache.items():
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"OK   {os.path.relpath(path, ROOT)}")
    print("PATCH_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
