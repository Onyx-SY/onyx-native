#!/usr/bin/env python3
"""离线验证 library 工具状态标记判定（修 P5：编辑成功却被记成 ❌）。

背景：storage.py 曾对整段 result 做 "error"/"失败" 子串匹配，而 edit_file/write_file
的返回里带 original_file（文件原文）——原文若含这些词就会被误判为失败。历史日志里
有 300+ 条「❌ edit_file」其实编辑成功。

运行: python3 test/virtual/test_library_status_marker.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from bin.ai_lib.storage import _tool_status_marker  # noqa: E402


def test_success_despite_failure_words_in_original():
    # 原文里含 "失败"/"error" → 不得误判为失败
    result = json.dumps({
        "result": "✅ 编辑成功: //help.md",
        "original_file": "## 错误处理\n编译失败时返回 error，请检查。",
        "file_path": "//help.md",
    }, ensure_ascii=False)
    assert _tool_status_marker(result) == "✅", _tool_status_marker(result)
    print("PASS 原文含「失败/error」的成功结果 → ✅")


def test_real_failures():
    assert _tool_status_marker("❌ File not found: /x") == "❌"
    assert _tool_status_marker("❌ 文件不存在: //") == "❌"
    assert _tool_status_marker(json.dumps({"result": "❌ 编辑失败: 非唯一"})) == "❌"
    print("PASS 真实失败 → ❌")


def test_empty_is_ok():
    assert _tool_status_marker("") == "✅"
    assert _tool_status_marker(None) == "✅"
    print("PASS 空结果 → ✅")


if __name__ == "__main__":
    test_success_despite_failure_words_in_original()
    test_real_failures()
    test_empty_is_ok()
    print("\nALL PASS")
