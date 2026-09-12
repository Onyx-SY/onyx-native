# -*- coding: utf-8 -*-
"""test_memory_tools_v2.py — MemoryRead/MemorySearch 多根 + 范围 + range + 缓存 单元测试

覆盖：
  1. 'all' 不搜 tmp/（当前会话工作文件）→ 消除自我污染
  2. scope=library 只搜 library
  3. 多记忆根：project 模式可读到 global 根（祖先 .ai_s）的记忆
  4. range 严格校验：非法/倒序 → 明确报错，不返回全文
  5. 大文件自截断 + 续读 range 指针
  6. 读缓存按文件指纹失效

运行: python3 -m unittest test.virtual.test_memory_tools_v2 -v
"""

import os
import shutil
import sys
import tempfile
import unittest

_ONYX_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ONYX_DIR not in sys.path:
    sys.path.insert(0, _ONYX_DIR)

import bin.ai_lib.memory_tools as mt  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="onyx_mt2_")
        self._orig = mt._MEM_HOME
        mt._MEMORY_QUERY_CACHE.clear()

    def tearDown(self):
        mt.set_memory_home(self._orig)
        mt._MEMORY_QUERY_CACHE.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mk(self, rel, content):
        p = os.path.join(self.tmp, ".ai_s", rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        return p


class TestSearchScope(_Base):
    def test_all_excludes_tmp(self):
        """'all' 不搜 tmp/（当前会话工作文件），避免自我污染"""
        mt.set_memory_home(self.tmp)
        self._mk("library/sess1.txt", "hello UNIQUEKEY world\n")
        self._mk("tmp/ai_request.txt", "hello UNIQUEKEY world\n")
        out = mt._exec_memory_search("UNIQUEKEY", "all")
        self.assertIn("sess1", out)
        self.assertNotIn("ai_request", out)

    def test_scope_library_only(self):
        """scope=library 只搜 library/，不搜 chat/"""
        mt.set_memory_home(self.tmp)
        self._mk("library/a.txt", "SCOPEKEY lib\n")
        self._mk("chat/first.json", '{"messages":[{"content":"SCOPEKEY chat"}]}')
        out = mt._exec_memory_search("SCOPEKEY", "all", scope="library")
        self.assertIn("a.txt", out)
        self.assertNotIn("first.json", out)


class TestMultiRoot(_Base):
    def test_read_from_global_root_in_project_mode(self):
        """project 模式可读到 global 根（祖先 .ai_s）下的记忆"""
        gbase = os.path.join(self.tmp, ".ai_s")
        gfile = os.path.join(gbase, "library", "globalsess.txt")
        os.makedirs(os.path.dirname(gfile), exist_ok=True)
        with open(gfile, "w", encoding="utf-8") as f:
            f.write("GLOBALROOT content line\n")
        project_home = os.path.join(gbase, "projects", "p1")
        os.makedirs(project_home, exist_ok=True)
        mt.set_memory_home(project_home)

        self.assertIn(gbase, mt._candidate_bases())
        out = mt._exec_memory_read("library/globalsess")
        self.assertIn("GLOBALROOT content line", out)


class TestRangeStrict(_Base):
    def test_invalid_range_errors_not_full(self):
        mt.set_memory_home(self.tmp)
        self._mk("library/r.txt", "\n".join(f"line{i}" for i in range(1, 51)) + "\n")
        out = mt._exec_memory_read("library/r", range_str="abc")
        self.assertIn("❌", out)
        self.assertNotIn("line50", out)  # 不应静默返回全文

    def test_start_gt_end_errors(self):
        mt.set_memory_home(self.tmp)
        self._mk("library/r2.txt", "\n".join(f"line{i}" for i in range(1, 11)) + "\n")
        out = mt._exec_memory_read("library/r2", range_str="8-2")
        self.assertIn("❌", out)

    def test_valid_range(self):
        mt.set_memory_home(self.tmp)
        self._mk("library/r3.txt", "\n".join(f"line{i}" for i in range(1, 11)) + "\n")
        out = mt._exec_memory_read("library/r3", range_str="3-5")
        self.assertIn("line3", out)
        self.assertIn("line5", out)
        self.assertNotIn("line6", out)


class TestTruncation(_Base):
    def test_large_file_truncated_with_continue_hint(self):
        mt.set_memory_home(self.tmp)
        big = "\n".join(f"L{i}:" + "x" * 100 for i in range(1, 1001)) + "\n"
        self._mk("library/big.txt", big)
        out = mt._exec_memory_read("library/big")
        self.assertIn('range="', out)   # 续读指针（中英文均含 range="）
        self.assertLess(len(out), 40000)


class TestCacheFingerprint(_Base):
    def test_cache_invalidated_on_change(self):
        mt.set_memory_home(self.tmp)
        p = self._mk("library/c.txt", "v1 AAA\n")
        out1 = mt._exec_memory_read("library/c")
        self.assertIn("v1 AAA", out1)
        with open(p, "w", encoding="utf-8") as f:
            f.write("v2 BBBBBB\n")
        out2 = mt._exec_memory_read("library/c")
        self.assertIn("v2 BBBBBB", out2)
        self.assertNotIn("v1 AAA", out2)


if __name__ == "__main__":
    unittest.main()
