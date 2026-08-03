# -*- coding: utf-8 -*-
"""
test_memory_search.py — MemorySearch 重构单元测试

覆盖：
  1. uuid 参数：真实 UUID / '<uuid>.txt' / 'library/<uuid>' 定位单个记忆会话
  2. 'all'（默认）全范围查找
  3. 无效 UUID → 明确报错
  4. 结果带行号（file:line:content）
  5. 复用文件搜索逻辑（_run_grep_lines）

运行:
  python -m pytest test/virtual/test_memory_search.py -v
"""

import json
import os
import sys
import tempfile
import unittest
import uuid as uuid_mod

_ONYX_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ONYX_DIR not in sys.path:
    sys.path.insert(0, _ONYX_DIR)

import bin.ai_cmd as m  # noqa: E402


class TestMemorySearchUuid(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="onyx_test_memory_")
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = self.home
        self.base = os.path.join(self.home, ".ai_s")
        os.makedirs(os.path.join(self.base, "library"), exist_ok=True)
        os.makedirs(os.path.join(self.base, "chat"), exist_ok=True)
        self.u1 = str(uuid_mod.uuid4())
        self.u2 = str(uuid_mod.uuid4())
        with open(os.path.join(self.base, "library", self.u1 + ".txt"), "w", encoding="utf-8") as f:
            f.write("line one\nkeyword here\nline three\n")
        with open(os.path.join(self.base, "library", self.u2 + ".txt"), "w", encoding="utf-8") as f:
            f.write("other\nkeyword also here\n")
        with open(os.path.join(self.base, "onyx_ai.md"), "w", encoding="utf-8") as f:
            f.write("persistent keyword note\n")
        with open(os.path.join(self.base, "chat", "first.json"), "w", encoding="utf-8") as f:
            json.dump({"messages": [{"session_uuid": self.u2}]}, f)
        m._MEMORY_QUERY_CACHE.clear()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.home)
        if self._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._old_home
        m._MEMORY_QUERY_CACHE.clear()

    def test_all_searches_whole_range(self):
        """'all' → 全范围查找（library + onyx_ai.md + chat）"""
        out = m._exec_memory_search("keyword", "all")
        self.assertIn(self.u1, out)
        self.assertIn(self.u2, out)
        self.assertIn("onyx_ai.md", out)

    def test_specific_uuid_searches_single_file(self):
        """真实 UUID → 只搜对应 library/<uuid>.txt"""
        out = m._exec_memory_search("keyword", self.u1)
        self.assertIn("matched files: 1", out)
        self.assertIn(self.u1, out)
        self.assertNotIn(self.u2, out)  # 另一个 UUID 的文件不应出现

    def test_uuid_with_txt_suffix(self):
        """兼容 '<uuid>.txt' 写法"""
        out = m._exec_memory_search("keyword", self.u1 + ".txt")
        self.assertIn("matched files: 1", out)

    def test_uuid_with_library_prefix(self):
        """兼容 'library/<uuid>' 写法"""
        out = m._exec_memory_search("keyword", "library/" + self.u1)
        self.assertIn("matched files: 1", out)

    def test_invalid_uuid_reports_error(self):
        """非 UUID 非 all 的值 → 明确报错"""
        out = m._exec_memory_search("keyword", "not-a-real-uuid")
        self.assertIn("❌", out)

    def test_results_include_line_numbers(self):
        """结果带行号 file:line:content"""
        out = m._exec_memory_search("keyword", self.u1)
        self.assertIn(f"{self.u1}.txt:2:keyword here", out)

    def test_no_match_message(self):
        """无匹配时给出提示"""
        out = m._exec_memory_search("zzz_nothing_here", "all")
        self.assertNotIn("❌", out)
        self.assertIn("ℹ️", out)

    def test_reuses_grep_logic(self):
        """复用文件搜索逻辑：_run_grep_lines 对单文件输出带文件名（-H）"""
        fp = os.path.join(self.base, "library", self.u1 + ".txt")
        raw = m._run_grep_lines("keyword", [fp], context=0, case_insensitive=True, timeout=30)
        self.assertIn(f"{fp}:2:keyword here", raw)


if __name__ == "__main__":
    unittest.main()
