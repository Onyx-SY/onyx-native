# -*- coding: utf-8 -*-
"""
test_i18n_bilingual.py — I18n「bilingual」别名语义单元测试（单语化，2026-09）

⚠️ 语义已变更（用户明确要求「中英不要拼在一块儿」）：
    旧行为：t(key, 'bilingual') → "中文 / English"（中英同显）
    新行为：'bilingual' / 'both' / 'zh_en' 一律**按当前配置语言**（language 文件，
            get_current_lang()）返回**单语**，绝不同时显示两种语言。
    见 bin/ai_lib/i18n.py 的 t() 与模块 docstring。

覆盖：
  1. bilingual 别名 → 按当前语言返回单语（且不含另一种语言、不含 " / " 拼接）
  2. 'both' / 'zh_en' 与 'bilingual' 等效
  3. 缺失键回退为 key 本身
  4. 单语模式下 {placeholder} 正常格式化（且只格式化当前语言那一份）
  5. 显式单语言模式（chinese / english）不受影响
  6. lang.json 原有键未被破坏

运行:
  python3 test/virtual/test_i18n_bilingual.py     → 输出 OK（unittest）
"""

import os
import sys
import unittest

_ONYX_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ONYX_DIR not in sys.path:
    sys.path.insert(0, _ONYX_DIR)

from bin.ai_lib.i18n import I18n, _
from bin.ai_lib import config as _cfg


class _FixedLang:
    """临时把 get_current_lang() 固定为 lang（i18n.t() 在调用时惰性 import 它）。"""

    def __init__(self, lang):
        self.lang = lang

    def __enter__(self):
        self._old = _cfg.get_current_lang
        _cfg.get_current_lang = lambda: self.lang
        return self

    def __exit__(self, *exc):
        _cfg.get_current_lang = self._old
        return False


class TestI18nBilingual(unittest.TestCase):
    def setUp(self):
        I18n.reset_instance()

    def test_bilingual_resolves_to_single_language(self):
        """bilingual 不再中英同显：按当前配置语言返回单语"""
        with _FixedLang("chinese"):
            zh = _("py_diag_ok", "bilingual", path="x.py")
            self.assertIn("✅ x.py: 语法编译通过", zh)
            self.assertNotIn("syntax OK", zh)
            self.assertNotIn(" / ", zh)
        with _FixedLang("english"):
            en = _("py_diag_ok", "bilingual", path="x.py")
            self.assertIn("syntax OK, no compile errors", en)
            self.assertNotIn("语法编译通过", en)
            self.assertNotIn(" / ", en)

    def test_both_alias(self):
        """'both' / 'zh_en' 与 'bilingual' 等效（同一语言下结果一致）"""
        for lang in ("chinese", "english"):
            with _FixedLang(lang):
                a = _("env_task", "both")
                b = _("env_task", "zh_en")
                c = _("env_task", "bilingual")
                self.assertEqual(a, c)
                self.assertEqual(b, c)
                self.assertNotIn(" / ", c)

    def test_missing_key_fallback(self):
        """缺失键回退为 key 本身"""
        with _FixedLang("chinese"):
            self.assertEqual(_("no_such_key_xyz", "bilingual"), "no_such_key_xyz")

    def test_bilingual_placeholder_formatting(self):
        """bilingual 下 {placeholder} 正常格式化（只出现当前语言那一份）"""
        with _FixedLang("chinese"):
            zh = _("py_syntax_error", "bilingual", line=9, msg="bad syntax", text="def x(:")
            self.assertIn("行 9", zh)
            self.assertIn("bad syntax", zh)
            self.assertNotIn("line 9", zh)
        with _FixedLang("english"):
            en = _("py_syntax_error", "bilingual", line=9, msg="bad syntax", text="def x(:")
            self.assertIn("line 9", en)
            self.assertIn("bad syntax", en)
            self.assertNotIn("行 9", en)

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
