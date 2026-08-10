# -*- coding: utf-8 -*-
"""
test_i18n_bilingual.py — I18n 双语模式单元测试

覆盖：
  1. t(key, 'bilingual') 返回 "中文 / English"
  2. 'both' / 'zh_en' 别名
  3. 缺失键回退
  4. 双语模式下的 {placeholder} 格式化

运行:
  python -m pytest test/virtual/test_i18n_bilingual.py -v
"""

import os
import sys
import unittest

_ONYX_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ONYX_DIR not in sys.path:
    sys.path.insert(0, _ONYX_DIR)

from bin.ai_lib.i18n import I18n, _


class TestI18nBilingual(unittest.TestCase):
    def setUp(self):
        I18n.reset_instance()

    def test_bilingual_returns_both_languages(self):
        """bilingual 模式返回 '中文 / English'"""
        text = _("py_diag_ok", "bilingual", path="x.py")
        self.assertIn("✅ x.py: 语法编译通过", text)
        self.assertIn("syntax OK, no compile errors", text)
        self.assertIn(" / ", text)

    def test_both_alias(self):
        """'both' 与 'zh_en' 别名等效"""
        a = _("env_task", "both")
        b = _("env_task", "zh_en")
        c = _("env_task", "bilingual")
        self.assertEqual(a, c)
        self.assertEqual(b, c)

    def test_missing_key_fallback(self):
        """缺失键回退为 key 本身"""
        self.assertEqual(_("no_such_key_xyz", "bilingual"), "no_such_key_xyz")

    def test_bilingual_placeholder_formatting(self):
        """双语模式下 {placeholder} 正常格式化"""
        text = _("py_syntax_error", "bilingual", line=9, msg="bad syntax", text="def x(:")
        self.assertIn("行 9", text)
        self.assertIn("line 9", text)
        self.assertIn("bad syntax", text)

    def test_single_language_still_works(self):
        """原有单语言模式不受影响"""
        zh = _("bye", "chinese")
        en = _("bye", "english")
        self.assertIn("退出", zh)
        self.assertIn("Exiting", en)

    def test_existing_keys_preserved(self):
        """原有键仍然存在（lang.json 未被破坏）"""
        i18n = I18n.get_instance()
        self.assertTrue(i18n.has_key("welcome", "chinese"))
        self.assertTrue(i18n.has_key("compact_queued", "english"))


if __name__ == "__main__":
    unittest.main()
