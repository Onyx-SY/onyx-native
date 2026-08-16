# -*- coding: utf-8 -*-
"""key.conf → key.json 迁移 + thinking 参数化回归测试。

背景：
- key.conf 改名 key.json：load_key_conf 优先读 key.json，旧 key.conf 自动迁移
  （写 key.json 成功后删除旧文件，迁移失败回退旧文件）。
- thinking 参数从平台硬编码改为 key.conf params 可覆盖：
  {"params": {"thinking": false}} → 关闭思考；true/on → 开启；dict → 透传。
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from bin.ai_lib import config  # noqa: E402
from bin.ai_lib.api import _resolve_thinking  # noqa: E402


class TestKeyJsonMigration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="onyx_keyjson_")
        self.json_path = os.path.join(self.tmp, "key.json")
        self.legacy_path = os.path.join(self.tmp, "key.conf")

    def tearDown(self):
        for p in (self.json_path, self.legacy_path):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass

    def _load(self):
        with mock.patch.object(config, "KEY_CONF_PATH", self.json_path), \
             mock.patch.object(config, "KEY_CONF_LEGACY_PATH", self.legacy_path):
            return config.load_key_conf()

    def test_loads_new_json(self):
        with open(self.json_path, "w", encoding="utf-8") as f:
            json.dump({"platform": "deepseek", "api_key": config._obfuscate("sk-test")}, f)
        conf = self._load()
        self.assertEqual(conf["api_key"], "sk-test")
        self.assertFalse(os.path.exists(self.legacy_path))

    def test_migrates_legacy_conf(self):
        with open(self.legacy_path, "w", encoding="utf-8") as f:
            json.dump({"platform": "deepseek", "api_key": config._obfuscate("sk-legacy"),
                       "model": "deepseek-v4-pro", "params": {"thinking": False}}, f)
        conf = self._load()
        self.assertEqual(conf["api_key"], "sk-legacy")
        self.assertEqual(conf["model"], "deepseek-v4-pro")
        self.assertEqual(conf["params"]["thinking"], False)
        # 迁移完成：key.json 已生成，旧 key.conf 已删除
        self.assertTrue(os.path.exists(self.json_path))
        self.assertFalse(os.path.exists(self.legacy_path))

    def test_legacy_plain_key_migrated(self):
        # 旧格式明文 key（无混淆前缀）也能迁移
        with open(self.legacy_path, "w", encoding="utf-8") as f:
            json.dump({"platform": "deepseek", "api_key": "sk-plain"}, f)
        conf = self._load()
        self.assertEqual(conf["api_key"], "sk-plain")
        self.assertTrue(os.path.exists(self.json_path))

    def test_broken_legacy_falls_back_empty(self):
        # 旧文件损坏 → 迁移失败 → 回退读旧文件也失败 → 空 dict（不崩溃）
        with open(self.legacy_path, "w", encoding="utf-8") as f:
            f.write("{not-json")
        self.assertEqual(self._load(), {})

    def test_no_file_returns_empty(self):
        self.assertEqual(self._load(), {})

    def test_save_writes_json_and_removes_legacy(self):
        with mock.patch.object(config, "KEY_CONF_PATH", self.json_path), \
             mock.patch.object(config, "KEY_CONF_LEGACY_PATH", self.legacy_path):
            with open(self.legacy_path, "w", encoding="utf-8") as f:
                f.write("old")
            config.save_key_conf("deepseek", "sk-new", "deepseek-v4-pro",
                                 {"thinking": True})
        self.assertTrue(os.path.exists(self.json_path))
        self.assertFalse(os.path.exists(self.legacy_path))
        with open(self.json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(config._deobfuscate(data["api_key"]), "sk-new")
        self.assertEqual(data["params"]["thinking"], True)


class TestResolveThinking(unittest.TestCase):
    """api.py _resolve_thinking：key.conf params 覆盖平台默认。"""

    PLAT = {"type": "enabled"}

    def test_none_uses_platform_default(self):
        self.assertEqual(_resolve_thinking(None, self.PLAT), self.PLAT)
        self.assertIsNone(_resolve_thinking(None, None))

    def test_bool(self):
        self.assertIsNone(_resolve_thinking(False, self.PLAT))
        self.assertEqual(_resolve_thinking(True, None), {"type": "enabled"})

    def test_string_off_forms(self):
        for off in ("off", "false", "0", "no", "disabled", "none", "OFF", "False"):
            self.assertIsNone(_resolve_thinking(off, self.PLAT), off)

    def test_string_on_forms(self):
        for on in ("on", "true", "1", "yes", "enabled", "ON", "True"):
            self.assertEqual(_resolve_thinking(on, None), {"type": "enabled"}, on)

    def test_dict_passthrough(self):
        custom = {"type": "disabled"}
        self.assertEqual(_resolve_thinking(custom, self.PLAT), custom)

    def test_unknown_string_falls_back_to_platform(self):
        self.assertEqual(_resolve_thinking("weird", self.PLAT), self.PLAT)
        self.assertIsNone(_resolve_thinking("weird", None))


if __name__ == "__main__":
    unittest.main()
