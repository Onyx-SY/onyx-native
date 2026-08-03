# -*- coding: utf-8 -*-
"""
test_lsp_auxiliary.py — LSP 纯辅助化单元测试

覆盖：
  1. AI 工具面：LspHover/LspDefinition/LspReferences/py_definition 已移除
  2. AI 工具面：仅保留 py_diagnostics / py_symbols / LspDiagnostics / LspSymbols
  3. MemorySearch 参数为 uuid（不再是 path）
  4. lsp_client 诊断过滤：只保留 error 级别（丢弃 warning/info/hint）
  5. LspDiagnostics 执行器只输出 error 级别
  6. LspSymbols 输出符号位置 @ path:line

运行:
  python -m pytest test/virtual/test_lsp_auxiliary.py -v
"""

import os
import sys
import unittest
from unittest.mock import MagicMock

_ONYX_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ONYX_DIR not in sys.path:
    sys.path.insert(0, _ONYX_DIR)

import bin.ai_cmd as m  # noqa: E402
from bin.ai_lib.tools import code_analysis as ca  # noqa: E402
from lib.lsp_client import LspClient, LspDiagnostic, LspSymbol  # noqa: E402


class TestLspToolSurface(unittest.TestCase):
    def setUp(self):
        self.tools = m.build_native_tools()
        self.names = {t["function"]["name"] for t in self.tools}

    def test_tools_live_in_tools_package(self):
        """工具定义已迁移到独立工具包 bin/ai_lib/tools/code_analysis.py"""
        def _make(name, desc, props, req, perm="ReadOnly"):
            return {"type": "function", "function": {"name": name,
                    "description": desc, "parameters": {"properties": props}},
                    "x_permission": perm}
        defs = ca.get_native_tools(_make)
        names = [t["function"]["name"] for t in defs]
        self.assertEqual(
            sorted(names),
            ["LspDiagnostics", "LspSymbols", "py_diagnostics", "py_symbols"],
        )
        # 权限为只读（纯辅助）
        self.assertTrue(all(t["x_permission"] == "ReadOnly" for t in defs))
        # 执行器位于工具包内
        self.assertTrue(callable(ca.exec_lsp_diagnostics))
        self.assertTrue(callable(ca.exec_lsp_symbols))
        self.assertTrue(callable(ca.exec_py_diagnostics))
        self.assertTrue(callable(ca.exec_py_symbols))
        self.assertTrue(hasattr(ca, "LSP_MANAGER"))
        self.assertTrue(callable(ca.shutdown_lsp))

    def test_subjective_tools_removed(self):
        """主观性 LSP 工具已从 AI 工具面移除"""
        for removed in ("LspHover", "LspDefinition", "LspReferences", "py_definition"):
            self.assertNotIn(removed, self.names)

    def test_auxiliary_tools_kept(self):
        """纯辅助工具保留：编译检查 + 符号位置"""
        for kept in ("py_diagnostics", "py_symbols", "LspDiagnostics", "LspSymbols", "search_file"):
            self.assertIn(kept, self.names)

    def test_memory_search_uses_uuid_param(self):
        """MemorySearch 参数是 uuid（真实 UUID 或 all），不再是 path"""
        ms = next(t for t in self.tools if t["function"]["name"] == "MemorySearch")
        props = ms["function"]["parameters"]["properties"]
        self.assertIn("uuid", props)
        self.assertNotIn("path", props)
        self.assertIn("all", props["uuid"]["description"])

    def test_lsp_diagnostics_description_mentions_errors_only(self):
        """LspDiagnostics 描述明确只报编译错误"""
        ld = next(t for t in self.tools if t["function"]["name"] == "LspDiagnostics")
        desc = ld["function"]["description"]
        self.assertIn("编译错误", desc)
        self.assertIn("compile error", desc.lower())


class TestLspClientErrorOnly(unittest.TestCase):
    def test_notification_filters_non_errors(self):
        """publishDiagnostics 只保留 error，丢弃 warning/info/hint"""
        client = LspClient("python", ["pyright-langserver", "--stdio"], "/tmp")
        client._handle_diagnostics_notification({
            "uri": "file:///tmp/x.py",
            "diagnostics": [
                {"range": {"start": {"line": 0, "character": 0}}, "severity": 1,
                 "message": "undefined name X"},
                {"range": {"start": {"line": 1, "character": 0}}, "severity": 2,
                 "message": "unused import os"},
                {"range": {"start": {"line": 2, "character": 0}}, "severity": 3,
                 "message": "info msg"},
                {"range": {"start": {"line": 3, "character": 0}}, "severity": 4,
                 "message": "hint msg"},
            ],
        })
        cache = client._diagnostics_cache["/tmp/x.py"]
        self.assertEqual(len(cache), 1)
        self.assertEqual(cache[0].severity, "error")
        self.assertEqual(cache[0].message, "undefined name X")


class TestLspExecutors(unittest.TestCase):
    def setUp(self):
        self._orig_get_client = ca.LSP_MANAGER.get_client
        self.fake = MagicMock()
        ca.LSP_MANAGER.get_client = lambda p: self.fake

    def tearDown(self):
        ca.LSP_MANAGER.get_client = self._orig_get_client

    def test_diagnostics_error_only(self):
        """执行器只输出 error 级别诊断"""
        self.fake.diagnostics.return_value = [
            LspDiagnostic(path="/x.py", line=3, character=5, severity="error",
                          message="undefined name X"),
            LspDiagnostic(path="/x.py", line=7, character=2, severity="warning",
                          message="unused import os"),
        ]
        out = ca.exec_lsp_diagnostics("/x.py")
        self.assertIn("undefined name X", out)
        self.assertNotIn("unused import os", out)

    def test_diagnostics_clean(self):
        """无 error 时报告通过"""
        self.fake.diagnostics.return_value = []
        out = ca.exec_lsp_diagnostics("/x.py")
        self.assertIn("✅", out)

    def test_symbols_include_locations(self):
        """符号输出包含精确位置 @ path:line"""
        self.fake.symbols.return_value = [
            LspSymbol(name="foo", kind="function", path="/x.py", line=4, character=0),
        ]
        out = ca.exec_lsp_symbols("/x.py")
        self.assertIn("foo", out)
        self.assertIn("@ /x.py:4", out)

    def test_no_server(self):
        """无对应语言服务器时给出双语提示"""
        ca.LSP_MANAGER.get_client = lambda p: None
        out = ca.exec_lsp_diagnostics("/x.rs")
        self.assertIn("⚠️", out)
        self.assertIn("language server", out)


if __name__ == "__main__":
    unittest.main()
