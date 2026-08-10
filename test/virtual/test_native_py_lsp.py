# -*- coding: utf-8 -*-
"""
test_native_py_lsp.py — Python 原生 ast LSP（NativePyClient）单元测试

覆盖：
  1. NativePyClient 由 LspManager 对 .py 文件统一分发（不启动外部语言服务器）
  2. diagnostics — 纯 compile 检查（真实语法错误报错）
  3. symbols — 归拢函数/类定义及位置
  4. hover — 位置处标识符定义签名
  5. definition — 本文件内符号定义位置
  6. references — 同名引用位置（含定义处）
  7. completion — 文件定义 + 关键字 + 内置
  8. format — 原生不支持安全格式化 → None（避免 ast.unparse 丢注释）
  9. 冷启动预热：hover/definition/references 首次调用即可用（无需先 diagnostics）

运行:
  python test/virtual/test_native_py_lsp.py -v
"""

import os
import sys
import tempfile
import unittest

_ONYX_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ONYX_DIR not in sys.path:
    sys.path.insert(0, _ONYX_DIR)

from lib.lsp_client import LspManager, NativePyClient, LspServerStatus  # noqa: E402

SAMPLE = (
    "import os\n"
    "\n"
    "class Greeter:\n"
    "    def hello(self, name: str) -> str:\n"
    "        return f'hi {name}'\n"
    "\n"
    "def top(x: int = 1):\n"
    "    def nested():\n"
    "        return x\n"
    "    return nested()\n"
    "\n"
    "def top():\n"
    "    pass\n"
)


class TestNativePyClientDispatch(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="onyx_test_native_lsp_")
        self.path = os.path.join(self.tmpdir, "sample.py")
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(SAMPLE)
        self.mgr = LspManager()

    def tearDown(self):
        self.mgr.shutdown_all()
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_py_gets_native_client(self):
        """Python 文件统一返回 NativePyClient，不启动外部服务器"""
        client = self.mgr.get_client(self.path)
        self.assertIsInstance(client, NativePyClient)
        self.assertEqual(client.language, "python")
        self.assertEqual(client.status, LspServerStatus.CONNECTED)

    def test_non_py_uses_external_map(self):
        """非 Python 文件仍走外部语言服务器映射（不崩溃）"""
        # .rs 无服务器时返回 None（环境无 rust-analyzer 时），不应是 NativePyClient
        info = self.mgr._get_server_cmd("x.rs")
        self.assertIsNotNone(info)

    def test_diagnostics_clean(self):
        client = self.mgr.get_client(self.path)
        self.assertEqual(client.diagnostics(self.path), [])

    def test_diagnostics_syntax_error(self):
        bad = os.path.join(self.tmpdir, "bad.py")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("def foo(:\n    pass\n")
        client = self.mgr.get_client(bad)
        diags = client.diagnostics(bad)
        self.assertEqual(len(diags), 1)
        self.assertEqual(diags[0].severity, "error")

    def test_symbols(self):
        client = self.mgr.get_client(self.path)
        syms = client.symbols(self.path)
        names = [s.name for s in syms]
        self.assertIn("Greeter", names)
        self.assertIn("hello", names)
        self.assertIn("top", names)
        self.assertIn("nested", names)
        greeter = next(s for s in syms if s.name == "Greeter")
        self.assertEqual(greeter.kind, "class")
        self.assertEqual(greeter.line, 3)
        top = next(s for s in syms if s.name == "top")
        self.assertEqual(top.kind, "function")

    def test_hover_definition_references_first_call(self):
        """冷启动修复：首次调用 hover/definition/references 即可用（无需先 diagnostics）"""
        client = self.mgr.get_client(self.path)
        # 不先调 diagnostics，直接调用（回归测试：此前首次调用全失败）
        h = client.hover(self.path, 6, 4)  # def top(x: int = 1) 的 top（0-based 第 7 行）
        self.assertIsNotNone(h)
        self.assertIn("def top(x: int", h.content)

        d = client.definition(self.path, 6, 4)
        self.assertTrue(any(l.line == 7 for l in d))  # LspLocation.line 为 1 起始

        r = client.references(self.path, 6, 4)
        self.assertGreaterEqual(len(r), 1)

    def test_definition_finds_other_location(self):
        """从引用处跳转到定义位置"""
        client = self.mgr.get_client(self.path)
        # 第 10 行 return nested() 中的 nested 引用（0-based 9,11）→ 定义在第 8 行（1-based 8）
        d = client.definition(self.path, 9, 11)
        self.assertTrue(any(l.line == 8 for l in d))

    def test_completion(self):
        client = self.mgr.get_client(self.path)
        items = client.completion(self.path, 2, 0)
        labels = [i.label for i in items]
        self.assertIn("Greeter", labels)
        self.assertIn("top", labels)
        self.assertIn("def", labels)
        # 内置函数存在（具体项可能因截断而异，只需有 builtin 类型项）
        self.assertTrue(any(i.kind == "builtin" for i in items))

    def test_format_none(self):
        """原生 ast 不支持安全格式化 → None"""
        client = self.mgr.get_client(self.path)
        self.assertIsNone(client.format(self.path))

    def test_native_client_has_all_lsp_methods(self):
        """NativePyClient 提供与 LspClient 一致的全部操作接口"""
        client = NativePyClient()
        for m in ("diagnostics", "hover", "definition", "references",
                  "completion", "symbols", "format", "shutdown"):
            self.assertTrue(callable(getattr(client, m)))


if __name__ == "__main__":
    unittest.main()
