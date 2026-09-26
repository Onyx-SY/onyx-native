#!/usr/bin/env python3
"""离线验证编辑工具「参数别名归一 + replace_all + 参数校验」。

背景：历史日志里反复出现
  - `❌ File not found: `（edit_file 收 path，却按 validate_edit 的 file_path 传参）
  - `❌ ❌ SEARCH text is empty`（反向传错文本参数名）
本测试确保：新旧命名都能工作，且缺参时给出明确错误。

运行: python3 test/virtual/test_edit_param_alias.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from bin.ai_lib import mcp_exec  # noqa: E402


def _write(content):
    fd, p = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    return p


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def _call(tool, params):
    return mcp_exec.execute_mcp_tool(tool, params)


def test_edit_file_aliases():
    p1 = _write("AAA\nBBB\n")
    ok, r = _call("edit_file", {"path": p1, "old_string": "AAA", "new_string": "XXX"})
    assert ok, r
    assert _read(p1).startswith("XXX"), _read(p1)

    p2 = _write("AAA\nBBB\n")
    ok, r = _call("edit_file", {"file_path": p2, "search": "AAA", "replace": "YYY"})
    assert ok, r
    assert _read(p2).startswith("YYY"), _read(p2)
    print("PASS edit_file：path/file_path + old_string/search 等价")


def test_validate_preview_aliases():
    p = _write("hello world\n")
    for params in ({"path": p, "old_string": "hello", "new_string": "hi"},
                   {"file_path": p, "search": "hello", "replace": "hi"}):
        ok, r = _call("validate_edit", params)
        assert ok, r
        assert not r.lstrip().startswith("❌"), r
        ok, r = _call("preview_edit", params)
        assert ok, r
        assert "hello" in r, r
    print("PASS validate_edit/preview_edit：两种命名等价")


def test_write_read_getinfo_alias():
    p = os.path.join(tempfile.mkdtemp(), "n.txt")
    ok, r = _call("write_file", {"file_path": p, "text": "abc\n"})
    assert ok, r
    assert _read(p) == "abc\n"
    ok, r = _call("read_file", {"file_path": p})
    assert ok and "abc" in r, r
    ok, r = _call("get_file_info", {"file_path": p})
    assert ok, r
    print("PASS write_file/read_file/get_file_info 别名")


def test_replace_all():
    # 默认：old_string 必须唯一，否则拒绝
    p0 = _write("x\nx\nx\n")
    ok, r = _call("edit_file", {"path": p0, "old_string": "x", "new_string": "y"})
    assert _read(p0) == "x\nx\nx\n", "非唯一时不应修改：" + repr(_read(p0))
    assert "not unique" in r.lower() or "❌" in r, r

    # 默认：唯一匹配 → 只替换该处
    p1 = _write("x\ny\nx\n")
    ok, r = _call("edit_file", {"path": p1, "old_string": "y", "new_string": "Z"})
    assert ok, r
    assert _read(p1) == "x\nZ\nx\n", repr(_read(p1))

    # replace_all=true：非唯一也全部替换
    p2 = _write("x\nx\nx\n")
    ok, r = _call("edit_file", {"path": p2, "old_string": "x", "new_string": "y", "replace_all": True})
    assert ok, r
    assert _read(p2) == "y\ny\ny\n", repr(_read(p2))
    print("PASS replace_all 生效（默认要求唯一 / true 全替换）")


def test_missing_args():
    cases = [
        ("edit_file", {"old_string": "a", "new_string": "b"}),
        ("validate_edit", {"old_string": "a", "new_string": "b"}),
        ("preview_edit", {"old_string": "a", "new_string": "b"}),
        ("write_file", {"content": "x"}),
        ("read_file", {}),
        ("get_file_info", {}),
    ]
    for tool, params in cases:
        ok, r = _call(tool, params)
        assert "File not found" not in r, f"{tool} 仍出现 File not found: {r}"
        assert "缺少参数" in r or "Missing parameter" in r, f"{tool} 未给出明确错误: {r}"
    # 有路径但缺文本
    p = _write("a\n")
    for tool in ("edit_file", "validate_edit", "preview_edit"):
        ok, r = _call(tool, {"path": p})
        assert "缺少参数" in r or "Missing parameter" in r, f"{tool} 缺文本未报错: {r}"
    print("PASS 缺参明确报错（不再 File not found / SEARCH empty）")


def test_quoted_path():
    p = _write("q\n")
    ok, r = _call("read_file", {"path": '"%s"' % p})
    assert ok and "q" in r, r
    ok, r = _call("get_file_info", {"path": "'%s'" % p})
    assert ok, r
    print("PASS 引号包裹路径已归一")


def test_grep_ignore_case_alias():
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "g.txt"), "w", encoding="utf-8") as f:
        f.write("Hello\n")
    for key in ("ignore_case", "-i"):
        ok, r = _call("grep_search", {"pattern": "hello", "path": d, key: True})
        assert ok and "Hello" in r, f"{key}: {r}"
    print("PASS grep_search ignore_case/-i 等价")


def test_directory_tree_alias():
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "a", "b", "c"), exist_ok=True)
    ok, r = _call("DirectoryTree", {"path": d, "max_depth": 3})
    assert ok and "c" in r, r
    ok, r = _call("DirectoryTree", {"path": d, "maxDepth": 3})
    assert ok and "c" in r, r
    print("PASS DirectoryTree max_depth/maxDepth 等价")


if __name__ == "__main__":
    test_edit_file_aliases()
    test_validate_preview_aliases()
    test_write_read_getinfo_alias()
    test_replace_all()
    test_missing_args()
    test_quoted_path()
    test_grep_ignore_case_alias()
    test_directory_tree_alias()
    print("\nALL PASS")
