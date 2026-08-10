# -*- coding: utf-8 -*-
"""
test_path_display.py — 前端路径/参数显示不截断单元测试

覆盖：
  1. _display_tool_params — 超长 path 完整显示，绝不截断
  2. 无 path 的巨型参数（如 write_file 的 content）回退限长并显式省略
  3. render_tool_table — 参数列不再截断（[:40] 已移除）
  4. MemorySearch 结果路径完整（不截断）

运行:
  python -m pytest test/virtual/test_path_display.py -v
"""

import json
import os
import sys
import tempfile
import unittest

_ONYX_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ONYX_DIR not in sys.path:
    sys.path.insert(0, _ONYX_DIR)

import bin.ai_cmd as m  # noqa: E402
from bin.ai_lib.ui import render_tool_table  # noqa: E402


class TestDisplayToolParams(unittest.TestCase):
    def test_long_path_not_truncated(self):
        """超长 path 完整显示（不截断 — 用户需要看清改的是哪个文件）"""
        long_path = "/data/user/0/com.termux/files/home/project/" + "a" * 300 + "/main.py"
        params = json.dumps({"path": long_path, "content": "x"})
        out = m._display_tool_params(params)
        self.assertEqual(out, f"path={long_path}")

    def test_long_pattern_not_truncated(self):
        """超长 pattern 完整显示"""
        long_pattern = "very_long_pattern_" + "b" * 200
        out = m._display_tool_params(json.dumps({"pattern": long_pattern}))
        self.assertEqual(out, f"pattern={long_pattern}")

    def test_pattern_preferred_over_uuid(self):
        """pattern 优先于 uuid 展示（搜索内容比会话 ID 更直观）"""
        out = m._display_tool_params(json.dumps({"pattern": "x", "uuid": "abc-123"}))
        self.assertEqual(out, "pattern=x")

    def test_content_only_params_capped(self):
        """无路径字段的巨型参数（write_file 的 content）回退限长并显式省略"""
        big = json.dumps({"content": "c" * 500})
        out = m._display_tool_params(big)
        self.assertLessEqual(len(out), 210)
        self.assertTrue(out.endswith("…") or len(out) <= 200)

    def test_short_raw_params_passthrough(self):
        """无法解析的短参数原样返回"""
        out = m._display_tool_params("path=/a/b/c")
        self.assertEqual(out, "path=/a/b/c")


class TestToolTableParams(unittest.TestCase):
    def test_params_column_not_truncated(self):
        """工具结果表格的参数列数据完整（[:40] 截断已移除，超长路径不再被裁剪）"""
        long_path = "/very/" + "long/" * 30 + "file.py"
        table = render_tool_table([
            {"name": "read_file", "params": f"path={long_path}",
             "status": "ok", "output": "x"},
        ])
        cell = table.columns[2]._cells[0]
        self.assertIn(long_path, str(cell))


class TestMemorySearchPathNotTruncated(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="onyx_test_path_display_")
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = self.home
        base = os.path.join(self.home, ".ai_s")
        os.makedirs(os.path.join(base, "library"), exist_ok=True)
        self.u = "abc-def-123"
        with open(os.path.join(base, "library", self.u + ".txt"), "w", encoding="utf-8") as f:
            f.write("one\ntarget here\nthree\n")
        m._MEMORY_QUERY_CACHE.clear()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.home)
        if self._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._old_home
        m._MEMORY_QUERY_CACHE.clear()

    def test_memory_search_returns_full_path(self):
        """MemorySearch 结果中的路径完整显示（含完整绝对路径）"""
        out = m._exec_memory_search("target", self.u)
        full = os.path.join(self.home, ".ai_s", "library", self.u + ".txt")
        self.assertIn(full, out)


if __name__ == "__main__":
    unittest.main()
