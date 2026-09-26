# -*- coding: utf-8 -*-
"""API Key 健壮性回归：strip / 校验 / 掩码 / 原子写 / 读取 strip。

对应本次修复：
- 输入卫生：strip 统一、密钥格式校验（非空、≥8、无空白）
- 存储：原子写 + 0600、空值拒绝、读取后 strip
- 展示：mask_api_key 短 key 不泄露完整串
"""
import json
import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from bin.ai_lib import config  # noqa: E402


class TestValidApiKey(unittest.TestCase):
    def test_accepts_normal(self):
        self.assertTrue(config._valid_api_key("sk-1234567890"))

    def test_rejects_empty_and_short(self):
        self.assertFalse(config._valid_api_key(""))
        self.assertFalse(config._valid_api_key("abc"))
        self.assertFalse(config._valid_api_key("   "))

    def test_rejects_whitespace_inside(self):
        self.assertFalse(config._valid_api_key("sk-abc def-123"))
        self.assertFalse(config._valid_api_key("sk-abc\n123456"))
        self.assertFalse(config._valid_api_key("sk-abc\t123456"))

    def test_rejects_non_str(self):
        self.assertFalse(config._valid_api_key(None))
        self.assertFalse(config._valid_api_key(12345678))


class TestMaskApiKey(unittest.TestCase):
    def test_short_key_not_leaked(self):
        for k in ("", "a", "abc", "abcdefgh"):
            masked = config.mask_api_key(k)
            self.assertTrue(set(masked) <= {"*"}, masked)
            if k:
                self.assertNotIn(k, masked)

    def test_long_key_masked(self):
        k = "sk-abcdefghijklmnopqrstuvwxyz"
        masked = config.mask_api_key(k)
        self.assertNotEqual(masked, k)
        self.assertTrue(masked.startswith("sk-a"))
        self.assertTrue(masked.endswith("wxyz"))
        self.assertIn("*", masked)
        self.assertNotIn("bcdefghijklmnopqrstuv", masked)


class TestAtomicWrite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="onyx_keyrob_")
        self.path = os.path.join(self.tmp, "out.json")

    def test_writes_valid_json(self):
        config._atomic_write_json(self.path, {"a": 1})
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"a": 1})

    def test_overwrite_and_no_tmp_leftover(self):
        config._atomic_write_json(self.path, {"a": 1})
        config._atomic_write_json(self.path, {"b": 2})
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"b": 2})
        leftovers = [n for n in os.listdir(self.tmp) if n.startswith(".tmp_key_")]
        self.assertEqual(leftovers, [])


class TestSaveLoadRobustness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="onyx_keyrob_")
        self.json_path = os.path.join(self.tmp, "key.json")
        self.legacy_path = os.path.join(self.tmp, "key.conf")

    def _patches(self):
        return (mock.patch.object(config, "KEY_CONF_PATH", self.json_path),
                mock.patch.object(config, "KEY_CONF_LEGACY_PATH", self.legacy_path))

    def test_save_strips_key(self):
        p1, p2 = self._patches()
        with p1, p2:
            config.save_key_conf("deepseek", "  sk-abcdef123  ", "m")
        with open(self.json_path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(config._deobfuscate(data["api_key"]), "sk-abcdef123")

    def test_save_rejects_empty(self):
        p1, p2 = self._patches()
        with p1, p2:
            with self.assertRaises(ValueError):
                config.save_key_conf("deepseek", "   ")

    def test_save_permissions_0600(self):
        p1, p2 = self._patches()
        with p1, p2:
            config.save_key_conf("deepseek", "sk-abcdef123", "m")
        mode = stat.S_IMODE(os.stat(self.json_path).st_mode)
        self.assertEqual(mode, 0o600)

    def test_load_strips_whitespace_after_deobfuscate(self):
        with open(self.json_path, "w", encoding="utf-8") as f:
            json.dump({"platform": "deepseek",
                       "api_key": config._obfuscate("  sk-abcdef123\n")}, f)
        p1, p2 = self._patches()
        with p1, p2:
            conf = config.load_key_conf()
        self.assertEqual(conf["api_key"], "sk-abcdef123")

    def test_load_plain_with_whitespace_stripped(self):
        with open(self.json_path, "w", encoding="utf-8") as f:
            json.dump({"platform": "deepseek", "api_key": "  sk-plain-12345  "}, f)
        p1, p2 = self._patches()
        with p1, p2:
            conf = config.load_key_conf()
        self.assertEqual(conf["api_key"], "sk-plain-12345")


if __name__ == "__main__":
    unittest.main()
