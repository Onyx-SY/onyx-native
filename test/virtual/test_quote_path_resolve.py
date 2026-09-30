#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""引号感知路径替换回归测试（离线）。

背景（2026-09 实测）：
  `./a.sh`（无 shebang 的文本脚本）会被改写成
  `python <ROOT_DIR>/onyx/cmd.py -c "source ./a.sh"`，
  送 PTY 前还要过 `replace_virtual_path_in_cmd → resolve_paths_in_multiline_text`。

  旧实现用 `line.split(' ')` 分词、`' '.join()` 拼回，完全不认引号：
  引号内的 `source ./a.sh` 被撕成 `'"source'` 和 `'./a.sh"'` 两个 token；
  右半截又被 `resolve_token_path` 的 `token.strip('\\'"')` 误判成「带引号」，
  于是 `quote_char = token[0]` 取到路径首字符 `'.'` 并贴到首尾 →
  `./<绝对路径>.`：① 收尾的 `"` 被 `.` 顶替 → 引号不闭合 →
  bash 进 PS2 续行提示符挂死；② 头部多出 `.` → 绝对路径变成错误的相对路径。

本测试锁死这两条不变量。

运行: python3 test/virtual/test_quote_path_resolve.py -v
"""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.parse import (  # noqa: E402
    handle_executable_path,
    resolve_paths_in_multiline_text,
    resolve_token_path,
)
from lib.resolve_path import FORBIDDEN_MSG  # noqa: E402

HOME = "/ROOT/home/u0"


def _resolver(p):
    """模拟虚拟路径解析：只有绝对路径 / ~ / ../ / ./ 才解析，其余原样。"""
    if p.startswith(("/", "~", "../")):
        return p
    if p.startswith("./"):
        return HOME + "/" + p[2:]
    return p


def _forbid(_p):
    """模拟 resolve：一律越界。"""
    return FORBIDDEN_MSG


class TestQuotedSegment(unittest.TestCase):
    """引号内的内容不能被撕开，引号必须原样保留。"""

    def test_quoted_multiword_arg_keeps_balanced_quotes(self):
        cmd = 'python /ROOT/onyx/cmd.py -c "source ./a.sh"'
        out = resolve_paths_in_multiline_text(cmd, _resolver)
        self.assertEqual(out.count('"'), 2, f"引号必须成对，实际: {out!r}")
        self.assertIn(f'"source {HOME}/a.sh"', out)
        self.assertNotIn("./ROOT", out, "不得出现 '.' 污染绝对路径")

    def test_result_is_valid_bash_syntax(self):
        """核心不变量：转换后的命令必须是合法 bash 语法（旧实现会挂在 PS2）。"""
        cmd = 'python /ROOT/onyx/cmd.py -c "source ./a.sh"'
        out = resolve_paths_in_multiline_text(cmd, _resolver)
        r = subprocess.run(["bash", "-n", "-c", out], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, f"bash 语法校验失败: {r.stderr.strip()}")

    def test_inner_whitespace_preserved(self):
        out = resolve_paths_in_multiline_text('echo "./a  ./b"', _resolver)
        self.assertEqual(out, f'echo "{HOME}/a  {HOME}/b"')

    def test_single_quoted_segment(self):
        out = resolve_paths_in_multiline_text("ls './a.sh'", _resolver)
        self.assertEqual(out, f"ls '{HOME}/a.sh'")


class TestTokenQuoteDetection(unittest.TestCase):
    """resolve_token_path 的引号判定。"""

    def test_paired_quotes_rebuilt(self):
        self.assertEqual(resolve_token_path('"./a.sh"', _resolver), f'"{HOME}/a.sh"')
        self.assertEqual(resolve_token_path("'./a.sh'", _resolver), f"'{HOME}/a.sh'")

    def test_single_sided_quote_not_mistaken_as_quoted(self):
        """`./a.sh"` 是半个引号 token：绝不能返回 `.<路径>.`（会吃掉尾引号）。"""
        r = resolve_token_path('./a.sh"', _resolver)
        self.assertFalse(r.startswith("."), f"不得以 '.' 开头（污染路径）: {r!r}")
        self.assertFalse(r.endswith("."), f"不得以 '.' 结尾（顶替引号）: {r!r}")

    def test_unquoted_still_resolved(self):
        self.assertEqual(resolve_token_path("./a.sh", _resolver), f"{HOME}/a.sh")


class TestUnchangedBehaviors(unittest.TestCase):
    """既有不变量不得回归。"""

    def test_forbidden_still_substituted(self):
        out = resolve_paths_in_multiline_text("cat /data/data/secret.txt", _forbid)
        self.assertNotIn("/data/data/", out)
        self.assertIn(FORBIDDEN_MSG, out)

    def test_plain_text_untouched(self):
        self.assertEqual(resolve_paths_in_multiline_text("cat a.txt", _resolver), "cat a.txt")

    def test_no_path_chars_fast_path(self):
        self.assertEqual(resolve_paths_in_multiline_text("echo hello", _resolver), "echo hello")

    def test_consecutive_spaces_preserved(self):
        self.assertEqual(
            resolve_paths_in_multiline_text("cat   ./a.sh", _resolver),
            f"cat   {HOME}/a.sh",
        )

    def test_multiline_preserved(self):
        out = resolve_paths_in_multiline_text("cat ./a.sh\nls ./b\n", _resolver)
        self.assertEqual(out, f"cat {HOME}/a.sh\nls {HOME}/b\n")

    def test_none_resolver_returns_text(self):
        self.assertEqual(resolve_paths_in_multiline_text("cat ./a.sh", None), "cat ./a.sh")


class TestEndToEndScriptExec(unittest.TestCase):
    """端到端：`./a.sh` 的改写产物经过路径替换后必须仍是合法命令。"""

    def test_full_pipeline_balanced(self):
        d = tempfile.mkdtemp(prefix="onyx_quote_")
        script = os.path.join(d, "a.sh")
        with open(script, "w", encoding="utf-8") as f:
            f.write("cd /\nls\n")          # 无 shebang → Onyx 自执行

        def resolve(tok):
            if not tok.startswith("./"):
                return tok
            return os.path.join(d, os.path.basename(tok))

        step1 = handle_executable_path("./a.sh", resolve, "/ROOT")
        self.assertEqual(step1, 'python /ROOT/onyx/cmd.py -c "source ./a.sh"')

        out = resolve_paths_in_multiline_text(step1, resolve)
        self.assertEqual(out.count('"'), 2, f"引号必须成对，实际: {out!r}")
        self.assertTrue(out.endswith('"'), f"必须以收尾引号结束: {out!r}")
        self.assertIn(script, out)
        r = subprocess.run(["bash", "-n", "-c", out], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, f"bash 语法校验失败: {r.stderr.strip()}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
