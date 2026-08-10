#!/usr/bin/env python3
"""离线验证 Agent 多任务结果分栏显示的正则（与 ai_cmd.py 工具循环内一致）。

运行: python3 test/virtual/test_agent_display_blocks.py
"""
import re

# 与 ai_cmd.py 工具循环内完全一致的分栏正则
_BLOCK_SPLIT = re.compile(r"(?=\n?【[^】]*子代理「[^」]*」(?:总结|失败)】)")


def _split(out):
    return [b.strip() for b in _BLOCK_SPLIT.split(out) if b.strip()]


def test_two_success_blocks():
    out = ("【探索子代理「并行演示：两个探索子代理#1」总结】\n"
           "应用为单文件 Flask 论坛，功能完整……\n\n"
           "【探索子代理「并行演示：两个探索子代理#2」总结】\n"
           "Go 项目结构、依赖……")
    blocks = _split(out)
    assert len(blocks) == 2, f"期望 2 块, 实际 {len(blocks)}"
    assert blocks[0].startswith("【探索子代理「并行演示：两个探索子代理#1」总结】")
    assert blocks[1].startswith("【探索子代理「并行演示：两个探索子代理#2」总结】")
    print("PASS 两个成功总结 → 2 个分栏")


def test_single_block():
    out = "【探索子代理「单任务」总结】\n只有这一个"
    blocks = _split(out)
    assert len(blocks) == 1, f"期望 1 块, 实际 {len(blocks)}"
    print("PASS 单任务 → 1 个分栏")


def test_mixed_success_failure():
    out = ("【探索子代理「任务A」总结】\n成功内容\n\n"
           "【探索子代理「任务B」失败】超时")
    blocks = _split(out)
    assert len(blocks) == 2, f"期望 2 块, 实际 {len(blocks)}"
    assert blocks[1].startswith("【探索子代理「任务B」失败】")
    print("PASS 成功+失败混合 → 2 个分栏")


def test_async_output_no_split():
    out = "✅ 已异步启动 2 个探索子代理（任务ID: a, b）。主 AI 可继续其他工作……"
    blocks = _split(out)
    assert len(blocks) == 1
    # 代码里还有 "】总结】/】失败】" 前置条件，异步文案不含 → 走普通单行回显
    assert "】总结】" not in out and "】失败】" not in out
    print("PASS 异步返回文案不触发分栏")


def test_summary_with_embedded_marker():
    """总结正文里恰好出现类似标题的行，不应被误切（要求完整匹配「子代理「…」」结构）。"""
    out = ("【探索子代理「任务A」总结】\n"
           "正文里提到：【另一个东西」总结】这是内容的一部分\n\n"
           "【探索子代理「任务B」总结】\n第二段")
    blocks = _split(out)
    assert len(blocks) == 2, f"期望 2 块, 实际 {len(blocks)}"
    print("PASS 正文含相似文本不误切")


if __name__ == "__main__":
    test_two_success_blocks()
    test_single_block()
    test_mixed_success_failure()
    test_async_output_no_split()
    test_summary_with_embedded_marker()
    print("\nALL PASS")
