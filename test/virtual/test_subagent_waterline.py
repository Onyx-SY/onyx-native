#!/usr/bin/env python3
"""子代理上下文水位强制收尾 + 上下文超限兜底 单元测试（离线，mock API）。

运行: python3 -m unittest test/virtual/test_subagent_waterline.py -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import bin.ai_lib.subagent as sa  # noqa: E402


class TestSubagentWaterline(unittest.TestCase):
    def test_over_waterline_goes_straight_to_final_round(self):
        import bin.ai_lib.api as api_mod
        import bin.ai_cmd as ai_cmd_mod

        _orig_est = sa._estimate_msgs_tokens
        _orig_api = api_mod.call_ai_api_sse
        _orig_exec = ai_cmd_mod.execute_mcp_tool
        sa._estimate_msgs_tokens = lambda messages: sa._SUBAGENT_TOKEN_WATERLINE + 1
        _api_calls = []

        def _fake_api(question="", messages=None, tools=None, **kw):
            _api_calls.append((list(messages), tools))
            return {"tool_calls": [], "txt": "## Explore Summary\n水位收尾", "answer": "yes", "_reasoning": ""}

        api_mod.call_ai_api_sse = _fake_api
        ai_cmd_mod.execute_mcp_tool = lambda name, params, *a, **kw: (True, "x")
        try:
            t = sa.ExploreTask("任务", agent_type="explore")
            mgr = sa.ExploreManager()
            mgr._execute(t)
            self.assertEqual(len(_api_calls), 1, "超水位首轮即强制收尾")
            self.assertFalse(_api_calls[0][1], "超水位时首轮应无工具")
            self.assertIn("水位收尾", t.summary)
            self.assertNotEqual(t.status, "error", "水位收尾不算失败")
        finally:
            sa._estimate_msgs_tokens = _orig_est
            api_mod.call_ai_api_sse = _orig_api
            ai_cmd_mod.execute_mcp_tool = _orig_exec

    def test_context_overflow_retries_with_final_round(self):
        import bin.ai_lib.api as api_mod
        import bin.ai_cmd as ai_cmd_mod

        _orig_est = sa._estimate_msgs_tokens
        _orig_api = api_mod.call_ai_api_sse
        _orig_exec = ai_cmd_mod.execute_mcp_tool
        sa._estimate_msgs_tokens = lambda messages: 100  # 水位正常
        _api_calls = []
        _round = [0]

        def _fake_api(question="", messages=None, tools=None, **kw):
            _round[0] += 1
            _api_calls.append(tools)
            if _round[0] == 1:
                return {"error": "maximum context length is 100000 tokens", "answer": "no"}
            return {"tool_calls": [], "txt": "## Explore Summary\n超限兜底", "answer": "yes", "_reasoning": ""}

        api_mod.call_ai_api_sse = _fake_api
        ai_cmd_mod.execute_mcp_tool = lambda name, params, *a, **kw: (True, "x")
        try:
            t = sa.ExploreTask("任务", agent_type="explore")
            mgr = sa.ExploreManager()
            mgr._execute(t)
            self.assertEqual(len(_api_calls), 2, "超限后应重试一轮收尾")
            self.assertFalse(_api_calls[1], "兜底轮应无工具")
            self.assertNotEqual(t.status, "error", "超限兜底不算失败")
            self.assertIn("超限兜底", t.summary)
        finally:
            sa._estimate_msgs_tokens = _orig_est
            api_mod.call_ai_api_sse = _orig_api
            ai_cmd_mod.execute_mcp_tool = _orig_exec


if __name__ == "__main__":
    unittest.main()
