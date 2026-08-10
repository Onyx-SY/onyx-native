#!/usr/bin/env python3
"""大小感知规划门禁 + JSON 片段反转义 单元测试（离线）。

运行: python3 -m unittest test/virtual/test_plan_gate.py -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from bin.ai_cmd import (  # noqa: E402
    plan_gate_blocked, _unescape_json_fragment,
    _PLAN_GATE_DESTRUCTIVE, _PLAN_GATE_SINGLE_WRITE_BYTES,
    _PLAN_GATE_CUMULATIVE_BYTES,
)


class TestPlanGate(unittest.TestCase):
    """大小感知规划门禁：小型修改放行，大型/破坏性拦截，确认后放行。"""

    def test_small_write_passes(self):
        blocked, budget = plan_gate_blocked("write_file", {"content": "x" * 100}, False, 0)
        self.assertFalse(blocked)
        self.assertEqual(budget, 100, "小写应计入累计预算")

    def test_big_single_write_blocked(self):
        blocked, _ = plan_gate_blocked(
            "write_file",
            {"content": "x" * (_PLAN_GATE_SINGLE_WRITE_BYTES + 1)},
            False, 0)
        self.assertTrue(blocked, "单次 >4KB 应拦截")

    def test_cumulative_budget_blocks(self):
        _, budget = plan_gate_blocked(
            "edit_file", {"old_string": "a", "new_string": "b" * 3000}, False, 0)
        self.assertEqual(budget, 3001)
        blocked, _ = plan_gate_blocked(
            "edit_file", {"old_string": "c", "new_string": "d" * 3000}, False, budget)
        self.assertFalse(blocked, "累计 6001 < 8192 不拦截")
        blocked, _ = plan_gate_blocked(
            "write_file", {"content": "e" * 3000}, False, budget + 6000)
        self.assertTrue(blocked, "累计 ≥8KB 应拦截")

    def test_destructive_always_blocked(self):
        for tool in _PLAN_GATE_DESTRUCTIVE:
            blocked, _ = plan_gate_blocked(tool, {}, False, 0)
            self.assertTrue(blocked, f"{tool} 破坏性操作未规划时应始终拦截")

    def test_plan_confirmed_bypasses(self):
        blocked, _ = plan_gate_blocked(
            "write_file", {"content": "x" * 999999}, True, 0)
        self.assertFalse(blocked, "计划确认后放行")

    def test_plan_mode_bypasses(self):
        blocked, _ = plan_gate_blocked("delete_file", {}, False, 0, mode="plan")
        self.assertFalse(blocked, "plan 模式由既有拦截逻辑处理，门禁不重复拦截")

    def test_non_write_tools_unaffected(self):
        blocked, budget = plan_gate_blocked("read_file", {"path": "a"}, False, 0)
        self.assertFalse(blocked)
        self.assertEqual(budget, 0, "非写类工具不计入预算")

    def test_undo_and_runcommand_exempt(self):
        blocked, _ = plan_gate_blocked("UndoLastEdit", {}, False, 0)
        self.assertFalse(blocked, "撤销是安全网，不应被门禁卡住")
        blocked, _ = plan_gate_blocked("RunCommand", {"command": "ls"}, False, 0)
        self.assertFalse(blocked, "危险命令已有自身确认机制")


class TestUnescapeJsonFragment(unittest.TestCase):
    """单遍反转义：字面量 \\n 不被误转成换行。"""

    def test_literal_backslash_n_preserved(self):
        # JSON 中字面量 \n（反斜杠+n）编码为 \\n（两个反斜杠 + n）
        self.assertEqual(_unescape_json_fragment(r"a\\nb"), "a\\nb")

    def test_real_escapes_decoded(self):
        self.assertEqual(_unescape_json_fragment(r"a\nb\tc\"d\\e"), "a\nb\tc\"d\\e")

    def test_unicode_escape(self):
        self.assertEqual(_unescape_json_fragment(r"\u4e2d\u6587"), "中文")

    def test_unknown_escape_kept(self):
        self.assertEqual(_unescape_json_fragment(r"\q"), "q")


if __name__ == "__main__":
    unittest.main()
