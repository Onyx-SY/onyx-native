#!/usr/bin/env python3
"""记忆工具路径硬化单元测试：绝对路径 / ../ 穿越必须被拒绝。

运行: python3 -m unittest test/virtual/test_memory_path.py -v
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from bin.ai_cmd import _resolve_memory_path  # noqa: E402
import bin.ai_cmd as ai_cmd_mod  # noqa: E402


class TestMemoryPathHardening(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="onyx_mem_")
        os.makedirs(os.path.join(self.tmp, ".ai_s", "library"), exist_ok=True)
        os.makedirs(os.path.join(self.tmp, ".ai_s", "chat"), exist_ok=True)
        self._orig_home = ai_cmd_mod.get_memory_home
        ai_cmd_mod.get_memory_home = lambda: self.tmp

    def tearDown(self):
        ai_cmd_mod.get_memory_home = self._orig_home
        shutil.rmtree(self.tmp)

    def test_library_uuid_ok(self):
        p = _resolve_memory_path("library/abc123")
        self.assertEqual(p, os.path.join(self.tmp, ".ai_s", "library", "abc123.txt"))

    def test_library_uuid_with_txt_ok(self):
        p = _resolve_memory_path("library/abc123.txt")
        self.assertEqual(p, os.path.join(self.tmp, ".ai_s", "library", "abc123.txt"))

    def test_onyx_ai_ok(self):
        p = _resolve_memory_path("onyx_ai")
        self.assertEqual(p, os.path.join(self.tmp, ".ai_s", "onyx_ai.md"))

    def test_absolute_path_outside_rejected(self):
        with self.assertRaises(ValueError):
            _resolve_memory_path("/etc/passwd")

    def test_traversal_rejected(self):
        with self.assertRaises(ValueError):
            _resolve_memory_path("library/../../.ssh/id_rsa")

    def test_chat_traversal_rejected(self):
        with self.assertRaises(ValueError):
            _resolve_memory_path("chat/../../x")

    def test_absolute_inside_base_allowed(self):
        # 绝对路径落在记忆根内：包含性是安全属性，格式不强制
        _inner = os.path.join(self.tmp, ".ai_s", "library", "x.txt")
        p = _resolve_memory_path(_inner)
        self.assertEqual(p, os.path.normpath(_inner))


if __name__ == "__main__":
    unittest.main()
