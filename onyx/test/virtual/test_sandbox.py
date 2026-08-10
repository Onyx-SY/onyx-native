"""
沙箱虚拟化单元测试

覆盖范围（仅活代码；engine/markup 标记系统已随标记语言移除）：
  1. security.py — check_sandbox_path() 路径校验
  2. api.py      — process_ai_result_fields() markup_blocks 默认值
  3. path_ops.py — get_virtual_path() / format_virtual_path()

运行:
  python -m pytest test/virtual/ -v
  或
  python -m unittest test/virtual/test_sandbox.py -v
"""

import os
import sys
import json
import copy
import tempfile
import unittest
from unittest.mock import patch, MagicMock, PropertyMock, call
from pathlib import Path

# ── 将 onyx/ 加入 sys.path（core/ lib/ bin/ 都在其下）──
_ONYX_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ONYX_DIR not in sys.path:
    sys.path.insert(0, _ONYX_DIR)


# ========================================================================
# 1. security.py — check_sandbox_path()
# ========================================================================

class TestCheckSandboxPath(unittest.TestCase):
    """check_sandbox_path() 路径校验"""

    def setUp(self):
        self.patches = []

    def _make_ctx(self, sandbox_enabled=True, sandbox_config_enable=True,
                   os_or_tbs="TBS", root_dir="/project"):
        """创建一个模拟的 AppContext"""
        ctx = MagicMock()
        ctx._SANDBOX_ENABLED = sandbox_enabled
        ctx.SANDBOX_CONFIG = {"enable": sandbox_config_enable}
        ctx.OS_OR_TBS = os_or_tbs
        ctx.ROOT_DIR = root_dir
        ctx.global_config = {
            "display_info": {"language": {"current": "chinese"}},
        }
        ctx.Fore.RED = ""
        ctx.Style.RESET_ALL = ""
        return ctx

    @patch("core.security.os.path.realpath")
    @patch("core.security.os.path.abspath")
    def test_path_inside_root_allowed(self, mock_abspath, mock_realpath):
        """路径在 ROOT_DIR 内 → 允许"""
        from core.security import check_sandbox_path

        mock_realpath.side_effect = lambda p: p
        mock_abspath.side_effect = lambda p: p

        ctx = self._make_ctx(root_dir="/project")

        result = check_sandbox_path(ctx, "/project/main.py", "req-1")
        self.assertTrue(result)

    @patch("core.security.os.path.realpath")
    @patch("core.security.os.path.abspath")
    def test_path_outside_root_allowed(self, mock_abspath, mock_realpath):
        """路径沙箱已停用：ROOT_DIR 外路径不再拦截（安全由 resolve_path 保证）"""
        from core.security import check_sandbox_path

        mock_realpath.side_effect = lambda p: p
        mock_abspath.side_effect = lambda p: p

        ctx = self._make_ctx(root_dir="/project")

        result = check_sandbox_path(ctx, "/etc/passwd", "req-2")
        self.assertTrue(result)

    @patch("core.security.os.path.realpath")
    @patch("core.security.os.path.abspath")
    def test_sandbox_disabled_all_allowed(self, mock_abspath, mock_realpath):
        """沙箱关闭 → 所有路径放行"""
        from core.security import check_sandbox_path

        mock_realpath.side_effect = lambda p: p
        mock_abspath.side_effect = lambda p: p

        ctx = self._make_ctx(sandbox_enabled=False)

        result = check_sandbox_path(ctx, "/etc/passwd", "req-3")
        self.assertTrue(result)

    @patch("core.security.os.path.realpath")
    @patch("core.security.os.path.abspath")
    def test_os_mode_all_allowed(self, mock_abspath, mock_realpath):
        """OS 模式 → 所有路径放行"""
        from core.security import check_sandbox_path

        mock_realpath.side_effect = lambda p: p
        mock_abspath.side_effect = lambda p: p

        ctx = self._make_ctx(os_or_tbs="OS")

        result = check_sandbox_path(ctx, "/etc/passwd", "req-4")
        self.assertTrue(result)

    @patch("core.security.os.path.realpath")
    @patch("core.security.os.path.abspath")
    def test_path_equals_root_allowed(self, mock_abspath, mock_realpath):
        """路径等于 ROOT_DIR → 允许"""
        from core.security import check_sandbox_path

        mock_realpath.side_effect = lambda p: p
        mock_abspath.side_effect = lambda p: p

        ctx = self._make_ctx(root_dir="/project")

        result = check_sandbox_path(ctx, "/project", "req-5")
        self.assertTrue(result)


# ========================================================================
# 2. api.py — process_ai_result_fields() markup_blocks 默认值
# ========================================================================

class TestMarkupBlocksField(unittest.TestCase):
    """process_ai_result_fields() 的 markup_blocks 字段处理"""

    def test_markup_blocks_default_when_missing(self):
        """markup_blocks 不存在 → 默认空列表"""
        from bin.ai_lib.api import process_ai_result_fields

        result = process_ai_result_fields({"txt": "hello"})
        self.assertIn("markup_blocks", result)
        self.assertEqual(result["markup_blocks"], [])

    def test_markup_blocks_preserved_when_present(self):
        """markup_blocks 已存在 → 保持原值"""
        from bin.ai_lib.api import process_ai_result_fields

        blocks = [{"type": "view", "path": "/a.py"}]
        result = process_ai_result_fields({"txt": "hi", "markup_blocks": blocks})
        self.assertEqual(result["markup_blocks"], blocks)

    def test_other_fields_untouched(self):
        """不影响其他已有字段"""
        from bin.ai_lib.api import process_ai_result_fields

        result = process_ai_result_fields({
            "answer": "yes",
            "txt": "hello world",
            "tool_calls": [{"name": "test"}],
        })
        self.assertEqual(result["answer"], "yes")
        self.assertEqual(result["txt"], "hello world")
        self.assertEqual(result["tool_calls"], [{"name": "test"}])
        self.assertEqual(result["markup_blocks"], [])

    def test_markup_blocks_survives_copy(self):
        """markup_blocks 在 dict copy 后仍保留（浅拷贝 — 列表引用共享）"""
        from bin.ai_lib.api import process_ai_result_fields

        original = {"markup_blocks": [{"type": "edit", "path": "/x.py"}]}
        result = process_ai_result_fields(original)
        # result 是 shallow copy，markup_blocks 列表仍是同一引用
        self.assertIs(result["markup_blocks"], original["markup_blocks"])
        # 但 result dict 本身不是 original
        self.assertIsNot(result, original)


# ========================================================================
# 3. path_ops.py — 虚拟/物理路径转换
# ========================================================================

class TestPathOpsVirtualPath(unittest.TestCase):
    """get_virtual_path() / format_virtual_path() 路径转换"""

    def setUp(self):
        self.ctx = MagicMock()
        self.ctx.ROOT_DIR = "/project"
        self.ctx.USER_HOME_DIR = "/project/home/user"
        self.ctx._SANDBOX_ENABLED = True
        self.ctx.OS_OR_TBS = "TBS"

    @patch("core.path_ops.os.path.realpath")
    @patch("core.path_ops.os.path.normpath")
    def test_project_root_is_slash(self, mock_normpath, mock_realpath):
        """ROOT_DIR → /"""
        from core.path_ops import get_virtual_path

        mock_normpath.side_effect = lambda p: p
        mock_realpath.side_effect = lambda p: p

        result = get_virtual_path(self.ctx, "/project")
        self.assertEqual(result, "/")

    @patch("core.path_ops.os.path.realpath")
    @patch("core.path_ops.os.path.normpath")
    def test_subdir_under_root(self, mock_normpath, mock_realpath):
        """ROOT_DIR/subdir → /subdir"""
        from core.path_ops import get_virtual_path

        mock_normpath.side_effect = lambda p: p
        mock_realpath.side_effect = lambda p: p

        result = get_virtual_path(self.ctx, "/project/src/main.py")
        self.assertEqual(result, "/src/main.py")

    @patch("core.path_ops.os.path.realpath")
    @patch("core.path_ops.os.path.normpath")
    def test_home_is_tilde(self, mock_normpath, mock_realpath):
        """USER_HOME_DIR → ~"""
        from core.path_ops import get_virtual_path

        mock_normpath.side_effect = lambda p: p
        mock_realpath.side_effect = lambda p: p

        result = get_virtual_path(self.ctx, "/project/home/user")
        self.assertEqual(result, "~")

    @patch("core.path_ops.os.path.realpath")
    @patch("core.path_ops.os.path.normpath")
    def test_home_subdir_is_tilde_path(self, mock_normpath, mock_realpath):
        """USER_HOME_DIR/subdir → ~/subdir"""
        from core.path_ops import get_virtual_path

        mock_normpath.side_effect = lambda p: p
        mock_realpath.side_effect = lambda p: p

        result = get_virtual_path(self.ctx, "/project/home/user/docs")
        self.assertEqual(result, "~/docs")

    @patch("core.path_ops.os.path.realpath")
    @patch("core.path_ops.os.path.normpath")
    def test_sandbox_disabled_passthrough(self, mock_normpath, mock_realpath):
        """沙箱关闭 → 物理路径直通"""
        from core.path_ops import get_virtual_path

        self.ctx._SANDBOX_ENABLED = False
        mock_normpath.side_effect = lambda p: p
        mock_realpath.side_effect = lambda p: p

        result = get_virtual_path(self.ctx, "/etc/passwd")
        self.assertEqual(result, "/etc/passwd")


class TestFormatVirtualPath(unittest.TestCase):
    """format_virtual_path() 路径缩短"""

    def test_short_path_unchanged(self):
        """≤15 字符路径不缩短"""
        from core.path_ops import format_virtual_path

        self.assertEqual(format_virtual_path("/a/b/c"), "/a/b/c")

    def test_long_path_truncated(self):
        """>15 字符路径中间截断"""
        from core.path_ops import format_virtual_path

        result = format_virtual_path("/project/src/main/very/deep/file.py", max_len=15)
        self.assertIn("...", result)
        self.assertLessEqual(len(result), 28)  # 放宽上限

    def test_root_unchanged(self):
        """"/" 不缩短"""
        from core.path_ops import format_virtual_path

        self.assertEqual(format_virtual_path("/"), "/")

    def test_tilde_unchanged(self):
        """"~" 不缩短"""
        from core.path_ops import format_virtual_path

        self.assertEqual(format_virtual_path("~"), "~")

    def test_special_paths_unchanged(self):
        """特殊标记路径不缩短"""
        from core.path_ops import format_virtual_path

        self.assertEqual(format_virtual_path("/Not in virtual path"), "/Not in virtual path")
        self.assertEqual(format_virtual_path("/（路径异常）"), "/（路径异常）")


# ========================================================================
# 4. 边缘情况
# ========================================================================

class TestEdgeCases(unittest.TestCase):
    """边界条件测试"""

    @patch("core.security.os.path.realpath")
    @patch("core.security.os.path.abspath")
    def test_root_traversal_allowed(self, mock_abspath, mock_realpath):
        """路径沙箱已停用：穿越尝试不再被二次拦截（越界防护由 resolve_path 的 FORBIDDEN_MSG 承担）"""
        from core.security import check_sandbox_path

        ctx = MagicMock()
        ctx._SANDBOX_ENABLED = True
        ctx.SANDBOX_CONFIG = {"enable": True}
        ctx.OS_OR_TBS = "TBS"
        ctx.ROOT_DIR = "/project"
        ctx.global_config = {"display_info": {"language": {"current": "chinese"}}}
        ctx.Fore.RED = ""
        ctx.Style.RESET_ALL = ""

        mock_realpath.side_effect = lambda p: os.path.realpath(p)
        mock_abspath.side_effect = lambda p: os.path.abspath(p)

        # /project/../etc/passwd → /etc/passwd（realpath 后逃逸）——沙箱已停用，直接放行
        result = check_sandbox_path(ctx, "/project/../etc/passwd", "req-trav")
        self.assertTrue(result)
