#!/usr/bin/env python3
"""resolve_path.py 的 C 库加速层决策单元测试（离线，mock C 库）。

安全模型：C 库仅作加速，不参与安全决策；凡 C 返回 FORBIDDEN_MSG 或原样返回
相对路径（其 should_resolve 不识别裸 `..`），一律回退 Python fallback 终审。

运行: python3 -m unittest test/virtual/test_resolve_path_c.py -v
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from lib import resolve_path as rp  # noqa: E402


class FakeCLib:
    """模拟 C 库：按场景返回字节串（None = 空指针）"""

    def __init__(self, out):
        self._out = out

    def resolve_path(self, path, root_dir, user_home, current_dir):
        if isinstance(self._out, bytes):
            return self._out
        # 可编程：以输入 path 为键
        return self._out.get(path.decode(), b"")


class TestResolvePathCAuthority(unittest.TestCase):
    """C 库输出必须经过 Python 合法性校验；未通过/不可信 → Python 终审"""

    def setUp(self):
        self._old_cwd = os.getcwd()
        self._tmp = tempfile.mkdtemp(prefix="onyx_rp_c_")
        self.root = os.path.realpath(self._tmp)
        self.home = os.path.realpath(os.path.join(self._tmp, "home", "user"))
        os.makedirs(self.home, exist_ok=True)
        os.makedirs(os.path.join(self.home, "proj"), exist_ok=True)
        rp.ROOT_DIR = self.root
        rp.USER_HOME_DIR = self.home
        rp.PATH_RESOLVE_CACHE.clear()
        os.chdir(os.path.join(self.home, "proj"))

    def tearDown(self):
        os.chdir(self._old_cwd)
        rp.C_LIB = None
        rp.C_LIB_AVAILABLE = False
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _install_c(self, out):
        rp.C_LIB = FakeCLib(out)
        rp.C_LIB_AVAILABLE = True

    # === 回归：cd .. 场景 ===

    def test_c_returns_unchanged_dotdot_falls_back_to_python(self):
        """C 库不认识裸 `..`（should_resolve=False）原样返回 → Python 终审解析到父目录"""
        self._install_c(b"..")
        r = rp.resolve_path("..")
        self.assertEqual(r, self.home, "C 返回相对路径原样，Python 应解析到合法父目录")

    def test_c_returns_forbidden_but_parent_is_legal_home(self):
        """C 库只认虚拟根内（home 在根外时误报越界）→ Python 终审放行 home 内父目录"""
        self._install_c(rp.FORBIDDEN_MSG.encode())
        r = rp.resolve_path("..")
        self.assertEqual(r, self.home, "父目录在 USER_HOME_DIR 内应合法")

    def test_c_returns_forbidden_and_python_also_forbidden(self):
        """目标确实越界（不在根也不在 home）→ 仍返回 FORBIDDEN_MSG"""
        self._install_c(rp.FORBIDDEN_MSG.encode())
        os.chdir(self._tmp)  # cwd 在根外且 home 外：.. 越界
        r = rp.resolve_path("..")
        self.assertEqual(r, rp.FORBIDDEN_MSG, "真实越界必须保持拦截")

    # === C 快路径：合法输出直接采纳 ===

    def test_c_legal_absolute_adopted(self):
        """C 返回根内绝对路径 → 校验通过，直接采纳（快路径）"""
        inside = os.path.join(self.root, "virt", "etc")
        os.makedirs(inside, exist_ok=True)
        self._install_c(inside.encode())
        r = rp.resolve_path("/virt/etc")
        self.assertEqual(r, inside)

    def test_c_illegal_absolute_not_adopted(self):
        """C 返回合法区外的绝对路径 → 不被采纳，Python 按原输入重新解析（C 无法注入）"""
        inside = os.path.join(self.root, "virt", "etc")
        os.makedirs(inside, exist_ok=True)
        evil = os.path.realpath(os.path.join(self._tmp, "..", "evil"))
        self._install_c(evil.encode())
        r = rp.resolve_path("/virt/etc")
        self.assertEqual(r, inside, "C 的非法输出应被丢弃，结果来自 Python 对原输入的解析")
        self.assertNotEqual(r, evil)

    def test_c_empty_result_falls_back(self):
        """C 返回空 → 走 Python 终审（等价于 C 不可用）"""
        self._install_c(b"")
        r = rp.resolve_path("..")
        self.assertEqual(r, self.home)

    # === 无 C 库：纯 Python 基线 ===

    def test_no_c_library_dotdot_works(self):
        """C 不可用时 `..` 正常工作（回归保护）"""
        rp.C_LIB_AVAILABLE = False
        rp.C_LIB = None
        r = rp.resolve_path("..")
        self.assertEqual(r, self.home)

    def test_no_c_library_outside_home_blocked(self):
        rp.C_LIB_AVAILABLE = False
        rp.C_LIB = None
        os.chdir(self._tmp)
        r = rp.resolve_path("..")
        self.assertEqual(r, rp.FORBIDDEN_MSG)


if __name__ == "__main__":
    unittest.main()
