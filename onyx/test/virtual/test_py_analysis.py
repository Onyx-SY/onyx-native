# -*- coding: utf-8 -*-
"""
test_py_analysis.py — py_analysis 重写（纯辅助，零主观判断）单元测试

覆盖：
  1. exec_py_diagnostics — 纯编译尝试：仅真实语法/编译错误报错
  2. 无启发式提示：未使用的导入 / 裸 except / 死代码 一律不报告
  3. exec_py_symbols — 归拢所有函数/类定义及其精确位置（path:line）
     （含嵌套函数、条件分支内定义、类方法）

运行:
  python -m pytest test/virtual/test_py_analysis.py -v
"""

import os
import sys
import tempfile
import unittest

_ONYX_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ONYX_DIR not in sys.path:
    sys.path.insert(0, _ONYX_DIR)

from bin.ai_lib.py_analysis import exec_py_diagnostics, exec_py_symbols


class TestPyDiagnosticsCompileOnly(unittest.TestCase):
    """exec_py_diagnostics：纯粹的编译尝试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="onyx_test_py_analysis_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def _write(self, name, content):
        path = os.path.join(self.tmpdir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_valid_file_no_errors(self):
        """语法正确的文件 → 报告编译通过，不产生任何报错"""
        path = self._write("good.py", "import os\n\nx = 1\n")
        out = exec_py_diagnostics(path)
        self.assertIn("✅", out)
        self.assertNotIn("❌", out)

    def test_unused_import_not_reported(self):
        """未使用的导入不再报告（主观性判断已彻底移除）"""
        path = self._write("unused_import.py", "import os\nimport sys\n\nx = 1\n")
        out = exec_py_diagnostics(path)
        self.assertIn("✅", out)
        self.assertNotIn("未使用的导入", out)
        self.assertNotIn("unused import", out)

    def test_bare_except_not_reported(self):
        """裸 except 不再报告（属于风格建议，非编译错误）"""
        path = self._write("bare_except.py", "try:\n    x = 1\nexcept:\n    pass\n")
        out = exec_py_diagnostics(path)
        self.assertIn("✅", out)
        self.assertNotIn("bare except", out)

    def test_dead_code_not_reported(self):
        """定义了但未使用的函数不再报告（跨文件引用无法判断，避免误报）"""
        path = self._write("dead_code.py", "def helper():\n    return 1\n")
        out = exec_py_diagnostics(path)
        self.assertIn("✅", out)
        self.assertNotIn("已定义但未使用", out)

    def test_syntax_error_reported(self):
        """真实语法错误 → 报告错误及行号"""
        path = self._write("bad.py", "def foo(:\n    pass\n")
        out = exec_py_diagnostics(path)
        self.assertIn("❌", out)
        self.assertIn("1", out)  # 行号

    def test_non_py_file(self):
        """非 .py 文件给出提示"""
        path = self._write("a.txt", "hello")
        out = exec_py_diagnostics(path)
        self.assertIn(".py", out)

    def test_missing_file(self):
        """文件不存在 → 报错"""
        out = exec_py_diagnostics(os.path.join(self.tmpdir, "nope.py"))
        self.assertIn("❌", out)


class TestPySymbolsAllDefinitions(unittest.TestCase):
    """exec_py_symbols：归拢所有函数/类定义及其位置"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="onyx_test_py_symbols_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def _write(self, name, content):
        path = os.path.join(self.tmpdir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_all_definitions_with_locations(self):
        """所有定义（顶层函数、类、方法、嵌套函数、条件分支内函数）都被归拢且带 path:line"""
        path = self._write("sample.py", (
            "class A:\n"
            "    def m(self):\n"
            "        pass\n"
            "\n"
            "def top(x: int):\n"
            "    def nested():\n"
            "        pass\n"
            "    return x\n"
            "\n"
            "if True:\n"
            "    def cond_fn():\n"
            "        pass\n"
        ))
        out = exec_py_symbols(path)
        for name in ("A", "m", "top", "nested", "cond_fn"):
            self.assertIn(name, out)
        # 每个定义都带精确位置 path:line
        self.assertIn(f"@ {path}:1-3", out)    # class A 行1-3
        self.assertIn(f"@ {path}:5-8", out)    # def top 行5-8
        self.assertIn(f"@ {path}:6-7", out)    # nested 行6-7
        self.assertIn(f"@ {path}:11-12", out)  # cond_fn 行11-12
        # 无未使用提示
        self.assertNotIn("未使用", out)

    def test_async_and_decorator(self):
        """async 函数与装饰器信息保留（来自 AST 的事实信息）"""
        path = self._write("async_sample.py", (
            "import asyncio\n"
            "\n"
            "@asyncio.coroutine\n"
            "async def fetch():\n"
            "    return 1\n"
        ))
        out = exec_py_symbols(path)
        self.assertIn("async `fetch()`", out)
        self.assertIn("@coroutine", out)
        self.assertIn(f"@ {path}:4-5", out)

    def test_syntax_error_blocks_symbols(self):
        """文件有语法错误 → symbols 同样报告编译错误"""
        path = self._write("bad.py", "def foo(:\n    pass\n")
        out = exec_py_symbols(path)
        self.assertIn("❌", out)

    def test_match_case_definition(self):
        """match-case 分支内定义的函数同样被归拢（Python 3.10+）"""
        if sys.version_info < (3, 10):
            self.skipTest("match-case 需要 Python 3.10+")
        path = self._write("match_sample.py", (
            "def dispatch(x):\n"
            "    match x:\n"
            "        case 1:\n"
            "            def case_one():\n"
            "                pass\n"
            "        case _:\n"
            "            def case_default():\n"
            "                pass\n"
        ))
        out = exec_py_symbols(path)
        self.assertIn("case_one", out)
        self.assertIn("case_default", out)
        self.assertIn(f"@ {path}:4-5", out)
        self.assertIn(f"@ {path}:7-8", out)

    def test_empty_file(self):
        """空文件提示"""
        path = self._write("empty.py", "")
        out = exec_py_symbols(path)
        self.assertIn("ℹ️", out)


if __name__ == "__main__":
    unittest.main()
