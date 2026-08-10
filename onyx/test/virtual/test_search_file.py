# -*- coding: utf-8 -*-
"""
test_search_file.py — search_file 工具单元测试

覆盖：
  1. 按文件名关键字递归查找（子串匹配，不区分大小写）
  2. glob 模式查找
  3. 自动跳过依赖/构建目录（node_modules/.git/__pycache__ 等）
  4. 返回完整路径（不截断）
  5. 无匹配 / 根目录不存在

运行:
  python -m pytest test/virtual/test_search_file.py -v
"""

import os
import sys
import tempfile
import unittest

_ONYX_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ONYX_DIR not in sys.path:
    sys.path.insert(0, _ONYX_DIR)

import bin.ai_cmd as m  # noqa: E402


class TestSearchFile(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="onyx_test_search_file_")
        os.makedirs(os.path.join(self.root, "sub", "node_modules"), exist_ok=True)
        os.makedirs(os.path.join(self.root, "sub", "__pycache__"), exist_ok=True)
        os.makedirs(os.path.join(self.root, ".git"), exist_ok=True)
        self.files = [
            os.path.join(self.root, "alpha.py"),
            os.path.join(self.root, "sub", "beta.py"),
            os.path.join(self.root, "sub", "node_modules", "skip.py"),
            os.path.join(self.root, "sub", "__pycache__", "skip2.py"),
            os.path.join(self.root, ".git", "config"),
        ]
        for f in self.files:
            with open(f, "w", encoding="utf-8") as fh:
                fh.write("x = 1\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root)

    def test_substring_search_returns_full_paths(self):
        """子串匹配，返回完整路径（不截断）"""
        out = m._exec_search_file("alpha", self.root)
        self.assertIn(os.path.join(self.root, "alpha.py"), out)

    def test_recursive_search(self):
        """递归查找子目录"""
        out = m._exec_search_file("beta", self.root)
        self.assertIn(os.path.join(self.root, "sub", "beta.py"), out)

    def test_case_insensitive(self):
        """不区分大小写"""
        out = m._exec_search_file("ALPHA", self.root)
        self.assertIn("alpha.py", out)

    def test_skips_dependency_dirs(self):
        """跳过 node_modules / __pycache__ / .git"""
        out = m._exec_search_file("skip", self.root)
        self.assertNotIn("node_modules", out)
        self.assertNotIn("__pycache__", out)
        self.assertNotIn(".git", out)

    def test_glob_pattern(self):
        """glob 模式：'*.py' 匹配所有 Python 文件（跳过依赖目录）"""
        out = m._exec_search_file("*.py", self.root)
        self.assertIn("alpha.py", out)
        self.assertIn("beta.py", out)
        self.assertNotIn("skip.py", out)
        self.assertNotIn("skip2.py", out)

    def test_no_match(self):
        """无匹配 → 提示"""
        out = m._exec_search_file("zzz_never_exists", self.root)
        self.assertIn("🔍", out)
        self.assertIn("zzz_never_exists", out)

    def test_missing_root(self):
        """根目录不存在 → 报错"""
        out = m._exec_search_file("x", os.path.join(self.root, "nope"))
        self.assertIn("❌", out)

    def test_defaults_to_cwd(self):
        """path 缺省时使用当前工作目录"""
        old = os.getcwd()
        os.chdir(self.root)
        try:
            out = m._exec_search_file("alpha")
            self.assertIn("alpha.py", out)
        finally:
            os.chdir(old)


if __name__ == "__main__":
    unittest.main()
