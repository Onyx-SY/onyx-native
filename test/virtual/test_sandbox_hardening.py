#!/usr/bin/env python3
"""沙盒硬化单元测试：symlink 逃逸拦截 + 双不存在绝对路径不再放行。

运行: python3 -m unittest test/virtual/test_sandbox_hardening.py -v
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from bin.ai_lib import sandbox  # noqa: E402


class TestSandboxHardening(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="onyx_sb_")
        self.outside = tempfile.mkdtemp(prefix="onyx_out_")
        sandbox.init(self.tmp, os.path.expanduser("~"), force=True)

    def tearDown(self):
        sandbox.deactivate()
        shutil.rmtree(self.tmp)
        shutil.rmtree(self.outside)

    def test_symlink_escape_blocked_by_is_within(self):
        """工作区内 symlink → 外部目录：realpath 后必须判定越界。"""
        link = os.path.join(self.tmp, "evil_link")
        os.symlink(self.outside, link)
        self.assertFalse(
            sandbox.is_within(os.path.join(link, "secret.txt")),
            "symlink 指向外部 → 应拦截")

    def test_symlink_inside_allowed(self):
        inner = os.path.join(self.tmp, "real_dir")
        os.makedirs(inner)
        link = os.path.join(self.tmp, "ok_link")
        os.symlink(inner, link)
        self.assertTrue(sandbox.is_within(os.path.join(link, "f.txt")))

    def test_nonexistent_absolute_path_mapped_into_root(self):
        """双不存在绝对路径：必须映射进沙盒根，不再原样放行（防写真实 FS 根）。"""
        v = "/nonexistent_abs_xyz/api"
        resolved = sandbox.resolve(v)
        self.assertTrue(sandbox.is_within(resolved), f"应映射进沙盒, 实际 {resolved}")

    def test_existing_real_path_mapped_into_root(self):
        v = "/etc/passwd"
        resolved = sandbox.resolve(v)
        self.assertTrue(sandbox.is_within(resolved), f"真实系统路径应映射进沙盒, 实际 {resolved}")

    def test_traversal_blocked(self):
        with self.assertRaises(sandbox.SandboxBlockError):
            sandbox.resolve("../outside_escape")

    def test_relative_path_ok(self):
        resolved = sandbox.resolve("src/main.py")
        self.assertEqual(resolved, os.path.join(self.tmp, "src", "main.py"))


if __name__ == "__main__":
    unittest.main()
