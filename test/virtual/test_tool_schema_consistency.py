#!/usr/bin/env python3
"""离线验证工具参数一致性（防回归）。

断言：
  1. schema 中不再残留旧命名：file_path / -i / maxDepth / uuid；
  2. edit_file / validate_edit / preview_edit 三者参数名完全一致；
  3. schema 声明的参数都被调度层读取（无「声明了却被静默忽略」的参数）；
  4. 别名归一辅助函数存在。

运行: python3 test/virtual/test_tool_schema_consistency.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from bin.ai_lib import native_tools as nt  # noqa: E402

# 整表转发（把整个 params 交给执行器）的工具，无法用静态推断
_PASS_THROUGH = {"web_search"}
# 由外部 MCP server / 插件定义的 schema，参数由其自行解析
_EXTERNAL_PREFIX = ("mcp_",)
_EXTERNAL = {"code_view", "system_monitor", "py_diagnostics", "py_symbols",
             "LspDiagnostics", "LspSymbols", "LspHover", "LspDefinition",
             "LspReferences", "LspCompletion", "LspFormat"}

_LEGACY_NAMES = {"file_path", "-i", "maxDepth", "uuid"}

# (handler 体内出现任一标记 → 视为覆盖了这组参数)
_ALIAS_GROUPS = [
    (("_path_arg(", "_PATH_ALIASES"), {"path", "file_path", "target", "filename"}),
    (("_OLD_ALIASES",), {"old_string", "search", "old_text", "find"}),
    (("_NEW_ALIASES",), {"new_string", "replace", "new_text", "replacement"}),
    (('"ignore_case"',), {"ignore_case", "-i"}),
    (('"max_depth"',), {"max_depth", "maxDepth"}),
    (('"session_id"',), {"session_id", "uuid"}),
    (('"content", "text"',), {"content", "text"}),
]


def _schemas():
    out = {}
    for t in nt.build_native_tools():
        fn = t.get("function", t)
        props = (fn.get("parameters", {}) or {}).get("properties", {}) or {}
        out[fn.get("name")] = set(props.keys())
    return out


def _src():
    return open(os.path.join(ROOT, "bin", "ai_lib", "mcp_exec.py"), encoding="utf-8").read()


def _handler_body(src, name):
    m = re.search(r'\n\s{8}"%s":\s(.*?)(?=\n\s{8}"|\n    \})' % re.escape(name), src, re.S)
    return m.group(1) if m else ""


def _handler_names(src):
    m = re.search(r"_BUILTIN_HANDLERS\s*=\s*\{(.*?)\n    \}", src, re.S)
    block = m.group(1) if m else ""
    return set(re.findall(r'\n\s{8}"([A-Za-z_][A-Za-z0-9_]*)":\s', "\n" + block))


def test_no_legacy_names():
    schemas = _schemas()
    bad = {n: sorted(p & _LEGACY_NAMES) for n, p in schemas.items() if p & _LEGACY_NAMES}
    assert not bad, f"schema 仍残留旧命名：{bad}"
    print("PASS 无旧命名残留（file_path / -i / maxDepth / uuid）")


def test_edit_tools_identical_params():
    schemas = _schemas()
    a = schemas["edit_file"] - {"replace_all"}
    b = schemas["validate_edit"]
    c = schemas["preview_edit"]
    assert a == b == c == {"path", "old_string", "new_string"}, (a, b, c)
    print("PASS edit_file/validate_edit/preview_edit 参数一致")


def test_no_silently_ignored_params():
    schemas, src = _schemas(), _src()
    handler_names = _handler_names(src)
    problems = {}
    for name, props in schemas.items():
        if name in _PASS_THROUGH or name in _EXTERNAL or name.startswith(_EXTERNAL_PREFIX):
            continue
        if name not in handler_names:
            continue
        body = _handler_body(src, name)
        covered = set(re.findall(r'p\.get\(\s*"([^"]+)"', body))
        covered |= set(re.findall(r'_arg\(\s*p\s*,\s*"([^"]+)"', body))
        for markers, group in _ALIAS_GROUPS:
            if any(mk in body for mk in markers):
                covered |= group
        unused = props - covered
        if unused:
            problems[name] = sorted(unused)
    assert not problems, f"schema 声明但调度层未读取：{problems}"
    print("PASS 无「声明了却被静默忽略」的参数")


def test_alias_helpers_exist():
    src = _src()
    for needle in ("def _arg(", "def _path_arg(", "def _bool_arg(",
                   "def _strip_wrapping_quotes(", "def _normalize_path_params(",
                   "_OLD_ALIASES = ", "_NEW_ALIASES = ", "_PATH_ALIASES = "):
        assert needle in src, f"mcp_exec 缺少：{needle}"
    print("PASS 别名归一辅助函数存在")


if __name__ == "__main__":
    test_no_legacy_names()
    test_edit_tools_identical_params()
    test_no_silently_ignored_params()
    test_alias_helpers_exist()
    print("\nALL PASS")
