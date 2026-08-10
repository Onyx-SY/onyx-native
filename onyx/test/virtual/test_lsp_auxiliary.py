# -*- coding: utf-8 -*-
"""
test_lsp_auxiliary.py — LSP 纯辅助化单元测试

覆盖：
  1. AI 工具面：LspHover/LspDefinition/LspReferences/LspCompletion/LspFormat 已补齐（ReadOnly）
  2. AI 工具面：py_diagnostics / py_symbols / LspDiagnostics / LspSymbols 保留
  3. MemorySearch 参数为 uuid（不再是 path）
  4. lsp_client 诊断过滤：只保留 error 级别（丢弃 warning/info/hint）
  5. LspDiagnostics 执行器只输出 error 级别
  6. LspSymbols 输出符号位置 @ path:line
  7. 新增执行器：hover/definition/references/completion/format

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
from lib.lsp_client import (
    LspClient, LspDiagnostic, LspSymbol,
    LspHoverResult, LspLocation, LspCompletionItem,  # noqa: E402
)


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
            ["LspCompletion", "LspDefinition", "LspDiagnostics", "LspFormat",
             "LspHover", "LspReferences", "LspSymbols",
             "py_diagnostics", "py_symbols"],
        )
        # 权限为只读（纯辅助）
        self.assertTrue(all(t["x_permission"] == "ReadOnly" for t in defs))
        # 执行器位于工具包内
        self.assertTrue(callable(ca.exec_lsp_diagnostics))
        self.assertTrue(callable(ca.exec_lsp_symbols))
        self.assertTrue(callable(ca.exec_lsp_hover))
        self.assertTrue(callable(ca.exec_lsp_definition))
        self.assertTrue(callable(ca.exec_lsp_references))
        self.assertTrue(callable(ca.exec_lsp_completion))
        self.assertTrue(callable(ca.exec_lsp_format))
        self.assertTrue(callable(ca.exec_py_diagnostics))
        self.assertTrue(callable(ca.exec_py_symbols))
        self.assertTrue(hasattr(ca, "LSP_MANAGER"))
        self.assertTrue(callable(ca.shutdown_lsp))

    def test_extended_lsp_tools_present(self):
        """扩展 LSP 工具面：hover/definition/references/completion/format 全部在场且只读"""
        for name in ("LspHover", "LspDefinition", "LspReferences",
                     "LspCompletion", "LspFormat"):
            self.assertIn(name, self.names)
            tool = next(t for t in self.tools if t["function"]["name"] == name)
            self.assertEqual(tool["x_permission"], "ReadOnly")
            props = tool["function"]["parameters"]["properties"]
            self.assertIn("path", props)
            # 定位类工具要求 line/character 为 integer（0 起始）
            if name in ("LspHover", "LspDefinition", "LspReferences", "LspCompletion"):
                self.assertEqual(props["line"]["type"], "integer")
                self.assertEqual(props["character"]["type"], "integer")

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
        """LspDiagnostics 描述明确只报编译错误（中/英任一语言）"""
        ld = next(t for t in self.tools if t["function"]["name"] == "LspDiagnostics")
        desc = ld["function"]["description"]
        self.assertTrue("编译错误" in desc or "compile error" in desc.lower())


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

    def test_hover_returns_content(self):
        """悬停提示输出类型签名/文档内容"""
        self.fake.hover.return_value = LspHoverResult(content="`def foo(a: int) -> str`", language="python")
        out = ca.exec_lsp_hover("/x.py", 3, 5)
        self.assertIn("def foo", out)
        self.assertIn("python", out)

    def test_hover_empty(self):
        """无悬停提示时给出提示"""
        self.fake.hover.return_value = None
        out = ca.exec_lsp_hover("/x.py", 3, 5)
        self.assertIn("无悬停提示", out)

    def test_definition_returns_locations(self):
        """跳转定义输出 path:line:character + 行预览"""
        self.fake.definition.return_value = [
            LspLocation(path="/y.py", line=10, character=4, preview="def foo(a):"),
        ]
        out = ca.exec_lsp_definition("/x.py", 3, 5)
        self.assertIn("/y.py:10:4", out)
        self.assertIn("def foo(a):", out)

    def test_references_returns_locations(self):
        """查找引用输出所有位置"""
        self.fake.references.return_value = [
            LspLocation(path="/x.py", line=3, character=5),
            LspLocation(path="/z.py", line=20, character=8),
        ]
        out = ca.exec_lsp_references("/x.py", 3, 5)
        self.assertIn("2", out)  # 计数
        self.assertIn("/z.py:20:8", out)

    def test_completion_returns_items(self):
        """代码补全输出建议项"""
        self.fake.completion.return_value = [
            LspCompletionItem(label="foo", kind="function", detail="-> str"),
        ]
        out = ca.exec_lsp_completion("/x.py", 4, 2)
        self.assertIn("foo", out)
        self.assertIn("-> str", out)

    def test_format_returns_text(self):
        """格式化输出全文"""
        self.fake.format.return_value = "def foo():\n    pass\n"
        out = ca.exec_lsp_format("/x.py")
        self.assertIn("def foo():", out)

    def test_format_empty(self):
        """无格式化结果时给出提示"""
        self.fake.format.return_value = None
        out = ca.exec_lsp_format("/x.py")
        self.assertIn("无格式化结果", out)

    def test_uri_roundtrip(self):
        """路径 → URI → 路径 往返一致（含空格/中文）"""
        client = LspClient("python", ["pyright-langserver", "--stdio"], "/tmp")
        p = "/tmp/my dir/测试.py"
        uri = client._path_to_uri(p)
        self.assertIn("my%20dir", uri)
        self.assertEqual(client._uri_to_path(uri), p)


if __name__ == "__main__":
    unittest.main()
