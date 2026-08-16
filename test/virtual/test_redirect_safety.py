#!/usr/bin/env python3
"""越界防御移除后的安全不变量测试（离线）。

设计（2026-08 用户拍板）：
- ❌ 沙箱越界硬拦截（safe.py check_path_permission_for_cmd 的 FORBIDDEN_MSG 早退）已移除；
- 越界安全由执行层两条转换兜底保证：
  1. 参数 token → resolve_paths_in_multiline_text 把 FORBIDDEN_MSG 原文替换进命令文本，
     bash 找不到该文件，命令必然失败（真实路径接触不到）；
  2. 重定向目标 → _append_redirect_to_cmd / _safe_redirect_target 把 FORBIDDEN 目标
     替换为 /dev/null 安全落点（重定向是原样拼接，转换层碰不到，必须单独兜底）。

运行: python3 test/virtual/test_redirect_safety.py -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from lib.parse import resolve_paths_in_multiline_text  # noqa: E402
from lib.parse_and_execute import (  # noqa: E402
    _append_redirect_to_cmd, _safe_redirect_target,
)
from lib.resolve_path import FORBIDDEN_MSG  # noqa: E402
from lib import safe  # noqa: E402


def _forbid(_p):
    """模拟 resolve：一律越界"""
    return FORBIDDEN_MSG


def _legal(p):
    """模拟 resolve：一律合法原样"""
    return p


class TestArgTokenCoverage(unittest.TestCase):
    """参数 token 由命令文本转换兜底"""

    def test_forbidden_arg_substituted_into_command(self):
        converted = resolve_paths_in_multiline_text(
            "cat /data/data/com.termux/files/home/secret.txt", _forbid)
        self.assertNotIn("/data/data/", converted)
        self.assertIn(FORBIDDEN_MSG, converted, "FORBIDDEN 原文应替换进命令，bash 必然找不到该文件")

    def test_legal_arg_unchanged(self):
        self.assertEqual(resolve_paths_in_multiline_text("cat a.txt", _legal), "cat a.txt")


class TestRedirectTargetCoverage(unittest.TestCase):
    """重定向目标是原样拼接的，必须单独兜底：FORBIDDEN → /dev/null"""

    def test_safe_redirect_target_forbidden(self):
        self.assertEqual(_safe_redirect_target("/data/data/com.termux/files/home/x", _forbid), "/dev/null")

    def test_safe_redirect_target_legal_unchanged(self):
        self.assertEqual(_safe_redirect_target("out.txt", _legal), "out.txt")
        self.assertEqual(_safe_redirect_target("/dev/null", _legal), "/dev/null")

    def test_safe_redirect_target_no_resolver(self):
        self.assertEqual(_safe_redirect_target("/data/x", None), "/data/x")

    def test_append_redirect_forbidden_to_devnull(self):
        rc = {"stdout": ("/data/data/com.termux/files/home/x", "w"),
              "stderr": ("/data/data/com.termux/files/home/e", "w"),
              "stdin": "/data/data/com.termux/files/home/i"}
        out = _append_redirect_to_cmd("echo hi", rc, _forbid)
        self.assertEqual(out, "echo hi > /dev/null 2> /dev/null < /dev/null")

    def test_append_redirect_legal_unchanged(self):
        rc = {"stdout": ("out.txt", "w"), "stderr": ("err.txt", "a")}
        out = _append_redirect_to_cmd("echo hi", rc, _legal)
        self.assertEqual(out, "echo hi > out.txt 2>> err.txt")

    def test_append_redirect_stderr_stdout_passthrough(self):
        rc = {"stderr": "STDOUT", "stdout": ("x.log", "w")}
        out = _append_redirect_to_cmd("cmd", rc, _forbid)
        self.assertEqual(out, "cmd > /dev/null 2>&1")

    def test_append_redirect_no_config(self):
        self.assertEqual(_append_redirect_to_cmd("cmd", {}, _forbid), "cmd")


class TestPermissionCheckNoLongerBlocks(unittest.TestCase):
    """safe.check_path_permission_for_cmd 不再对 FORBIDDEN_MSG 硬拦截"""

    def setUp(self):
        self._old_cfg = safe.PERM_PATH_CONFIG
        safe.PERM_PATH_CONFIG = []  # 无细颗粒规则：仅验证 FORBIDDEN 不再触发早退

    def tearDown(self):
        safe.PERM_PATH_CONFIG = self._old_cfg

    def test_forbidden_paths_no_longer_blocked(self):
        allowed = safe.check_path_permission_for_cmd(
            "cat", [FORBIDDEN_MSG, "/legal/path"], "user", None,
            log_error_func=None, request_id="t")
        self.assertTrue(allowed, "FORBIDDEN_MSG 不应再导致命令被拒（越界由执行层转换兜底）")


if __name__ == "__main__":
    unittest.main()
