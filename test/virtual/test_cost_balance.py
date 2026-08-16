#!/usr/bin/env python3
"""cost.py 余额解析单元测试（离线）。

回归：DeepSeek /user/balance 返回 balance_infos[].total_balance（字符串），
旧实现读 data["balance"]（字段不存在）→ 恒 0.00，显示「💰 0.00 CNY」。

运行: python3 test/virtual/test_cost_balance.py -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from bin.ai_lib.cost import _parse_deepseek_balance  # noqa: E402


class TestParseDeepSeekBalance(unittest.TestCase):
    """官方响应形态（api-docs.deepseek.com/api/get-user-balance）"""

    def test_official_response_shape(self):
        data = {
            "is_available": True,
            "balance_infos": [{
                "currency": "CNY",
                "total_balance": "110.00",
                "granted_balance": "10.00",
                "topped_up_balance": "100.00",
            }],
        }
        bal, cur = _parse_deepseek_balance(data)
        self.assertEqual((bal, cur), (110.00, "CNY"))

    def test_multiple_currencies_picks_max(self):
        data = {
            "is_available": True,
            "balance_infos": [
                {"currency": "CNY", "total_balance": "110.00"},
                {"currency": "USD", "total_balance": "0.00"},
            ],
        }
        bal, cur = _parse_deepseek_balance(data)
        self.assertEqual((bal, cur), (110.00, "CNY"))

    def test_legacy_balance_field(self):
        bal, cur = _parse_deepseek_balance({"balance": 88.5, "currency": "CNY"})
        self.assertEqual((bal, cur), (88.5, "CNY"))

    def test_empty_returns_zero(self):
        bal, cur = _parse_deepseek_balance({})
        self.assertEqual((bal, cur), (0.0, "CNY"))

    def test_malformed_infos_returns_zero(self):
        bal, cur = _parse_deepseek_balance({"balance_infos": [{"currency": "CNY"}]})
        self.assertEqual((bal, cur), (0.0, "CNY"))


if __name__ == "__main__":
    unittest.main()
