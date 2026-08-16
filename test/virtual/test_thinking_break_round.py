# -*- coding: utf-8 -*-
"""思考断裂回归测试：纯思考轮不得写入 content=None 的 assistant 消息。

事故：超长思考（finish_reason=length）只输出 reasoning_content → 历史写入
{"role":"assistant","content":None,"reasoning_content":...} → 下一轮请求被服务器
以 400 "Invalid assistant message: content or tool_calls must be set" 拒绝，
且坏消息留在历史中导致会话卡死（AutoCompact 保留最近 8 轮原话，救不了）。
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from bin.ai_lib.api import strip_empty_assistant_messages  # noqa: E402
from bin import ai_cmd  # noqa: E402


class TestStripEmptyAssistantMessages(unittest.TestCase):
    """api.py 发送前清洗：content 与 tool_calls 均为空的 assistant 消息整条剔除。"""

    def test_keeps_normal_messages(self):
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello", "reasoning_content": "think"},
        ]
        clean, removed = strip_empty_assistant_messages(msgs)
        self.assertEqual(removed, 0)
        self.assertEqual(len(clean), 2)

    def test_keeps_tool_call_round(self):
        # DeepSeek thinking 模式合法形态：content null + tool_calls
        msgs = [{"role": "assistant", "content": None,
                 "tool_calls": [{"id": "t1", "function": {"name": "x", "arguments": "{}"}}],
                 "reasoning_content": "思考"}]
        clean, removed = strip_empty_assistant_messages(msgs)
        self.assertEqual(removed, 0)
        self.assertEqual(len(clean), 1)

    def test_drops_reasoning_only_message(self):
        # 事故原型：思考断裂轮 → content None + reasoning_content、无 tool_calls
        msgs = [
            {"role": "user", "content": "开始"},
            {"role": "assistant", "content": None, "reasoning_content": "超长思考..."},
        ]
        clean, removed = strip_empty_assistant_messages(msgs)
        self.assertEqual(removed, 1)
        self.assertEqual(len(clean), 1)
        self.assertEqual(clean[0]["role"], "user")

    def test_drops_empty_string_content(self):
        msgs = [{"role": "assistant", "content": "", "reasoning_content": "x"}]
        clean, removed = strip_empty_assistant_messages(msgs)
        self.assertEqual(removed, 1)
        self.assertEqual(len(clean), 0)

    def test_keeps_empty_user_message(self):
        msgs = [{"role": "user", "content": ""}]
        clean, removed = strip_empty_assistant_messages(msgs)
        self.assertEqual(removed, 0)
        self.assertEqual(len(clean), 1)

    def test_keeps_assistant_with_content_and_no_tool_calls(self):
        msgs = [{"role": "assistant", "content": "正文", "reasoning_content": "思考"}]
        clean, removed = strip_empty_assistant_messages(msgs)
        self.assertEqual(removed, 0)
        self.assertEqual(len(clean), 1)


class TestShouldAppendReplyAssistant(unittest.TestCase):
    """ai_cmd.py 根修：纯思考轮（无正文、无工具调用）不写入历史。"""

    def test_normal_reply_appends(self):
        self.assertTrue(ai_cmd._should_append_reply_assistant("回答", []))

    def test_reasoning_only_does_not_append(self):
        # 事故原型：_ai_txt 为空、_reasoning 非空、无 tool_calls
        self.assertFalse(ai_cmd._should_append_reply_assistant("", []))

    def test_both_empty_does_not_append(self):
        self.assertFalse(ai_cmd._should_append_reply_assistant("", []))

    def test_tool_call_round_handled_by_other_branch(self):
        # 带工具调用时由 tool_calls 分支写入（content null 合法），此分支不写
        self.assertFalse(ai_cmd._should_append_reply_assistant("正文", [{"id": "t1"}]))

    def test_whitespace_only_reply_does_not_append(self):
        self.assertFalse(ai_cmd._should_append_reply_assistant("   ", []))


if __name__ == "__main__":
    unittest.main()
