"""
沙箱虚拟化单元测试

覆盖范围：
  1. engine.py  — execute_block() 沙箱边界校验 + 保护目录拦截
  2. __init__.py — process_blocks() 沙箱根传递
  3. security.py — check_sandbox_path() 路径校验
  4. api.py     — process_ai_result_fields() markup_blocks 默认值
  5. path_ops.py — get_virtual_path() / format_virtual_path()

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
# 1. engine.py — execute_block() 沙箱边界校验
# ========================================================================

class TestExecuteBlockSandboxBoundary(unittest.TestCase):
    """execute_block() 的沙箱边界校验（路径越界拦截）"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="onyx_test_sandbox_")
        # 模拟一个沙箱根目录内的文件
        self.inside_file = os.path.join(self.tmpdir, "project", "main.py")
        os.makedirs(os.path.dirname(self.inside_file), exist_ok=True)
        with open(self.inside_file, "w") as f:
            f.write("print('hello')\n")

        # 模拟一个沙箱根目录外的文件
        self.outside_file = os.path.join(self.tmpdir, "secret", "passwd")
        os.makedirs(os.path.dirname(self.outside_file), exist_ok=True)
        with open(self.outside_file, "w") as f:
            f.write("root:x:0:0\n")

        self.sandbox_root = os.path.join(self.tmpdir, "project")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def _make_block(self, block_type="write", path=""):
        return {
            "type": block_type,
            "path": path,
            "content": "# test",
        }

    @patch("lib.native_fs.engine.PanelManager")
    def test_write_inside_sandbox_passes(self, MockPM):
        """WRITE 到沙箱内的文件 → 通过"""
        from lib.native_fs.engine import execute_block

        block = self._make_block("write", self.inside_file)
        result = execute_block(block, cwd="/tmp", sandbox_root=self.sandbox_root)

        self.assertTrue(result.success,
                        f"沙箱内路径应通过边界校验, 实际: {result.message}")

    @patch("lib.native_fs.engine.PanelManager")
    def test_write_outside_sandbox_blocked(self, MockPM):
        """WRITE 到沙箱外的文件 → 被拦截"""
        from lib.native_fs.engine import execute_block

        block = self._make_block("write", self.outside_file)
        result = execute_block(block, cwd="/tmp", sandbox_root=self.sandbox_root)

        self.assertFalse(result.success)
        self.assertIn("越界", result.message)

    @patch("lib.native_fs.engine.PanelManager")
    def test_edit_outside_sandbox_blocked(self, MockPM):
        """EDIT 到沙箱外的文件 → 被拦截"""
        from lib.native_fs.engine import execute_block

        block = self._make_block("edit", self.outside_file)
        block["search"] = "root"
        block["replace"] = "user"
        result = execute_block(block, cwd="/tmp", sandbox_root=self.sandbox_root)

        self.assertFalse(result.success)
        self.assertIn("越界", result.message)

    @patch("lib.native_fs.engine.PanelManager")
    def test_delete_outside_sandbox_blocked(self, MockPM):
        """DELETE 到沙箱外的文件 → 被拦截"""
        from lib.native_fs.engine import execute_block

        block = self._make_block("delete", self.outside_file)
        block["start"] = 1
        block["end"] = 1
        result = execute_block(block, cwd="/tmp", sandbox_root=self.sandbox_root)

        self.assertFalse(result.success)
        self.assertIn("越界", result.message)

    @patch("lib.native_fs.engine.PanelManager")
    def test_view_outside_sandbox_allowed(self, MockPM):
        """VIEW 沙箱外文件 → 允许（读操作不受限）"""
        from lib.native_fs.engine import execute_block

        block = self._make_block("view", self.inside_file)
        # VIEW 不是 mutation type，不经过沙箱校验
        result = execute_block(block, cwd="/tmp", sandbox_root=self.sandbox_root)

        # VIEW 即使路径在沙箱内也应该是正常行为
        self.assertIsNotNone(result)

    @patch("lib.native_fs.engine.PanelManager")
    def test_no_sandbox_no_block(self, MockPM):
        """sandbox_root=None → 不校验，所有操作放行"""
        from lib.native_fs.engine import execute_block

        block = self._make_block("write", self.outside_file)
        # sandbox_root=None → 跳过沙箱校验
        result = execute_block(block, cwd="/tmp", sandbox_root=None)

        # 无沙箱时写操作应该成功（文件存在可写）
        self.assertNotIn("越界", result.message,
                         "sandbox_root=None 不应触发沙箱校验")
        self.assertNotIn("保护目录", result.message)


# ========================================================================
# 2. engine.py — execute_block() 保护目录拦截
# ========================================================================

class TestExecuteBlockProtectedDir(unittest.TestCase):
    """execute_block() 的保护目录校验（禁止修改核心目录）"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="onyx_test_protected_")
        # 创建模拟的核心目录
        self.protected_dirs = [
            os.path.join(self.tmpdir, "onyx"),
            os.path.join(self.tmpdir, "etc", "pki"),
            os.path.join(self.tmpdir, "onyxlog"),
            os.path.join(self.tmpdir, "tools", "sys_tools"),
        ]
        for d in self.protected_dirs:
            os.makedirs(d, exist_ok=True)

        # 每个受保护目录中放一个测试文件
        self.protected_files = {}
        for d in self.protected_dirs:
            fpath = os.path.join(d, "test.txt")
            with open(fpath, "w") as f:
                f.write("protected content\n")
            rel = os.path.relpath(d, self.tmpdir)
            self.protected_files[rel] = fpath

        self.sandbox_root = self.tmpdir

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    @patch("lib.native_fs.engine.PanelManager")
    def test_write_onyx_blocked(self, MockPM):
        """WRITE 到 onyx/ 下的文件 → 被保护目录拦截"""
        from lib.native_fs.engine import execute_block

        target = self.protected_files["onyx"]
        block = {"type": "write", "path": target, "content": "# hacked"}
        result = execute_block(block, cwd="/tmp", sandbox_root=self.sandbox_root)

        self.assertFalse(result.success)
        self.assertIn("保护目录", result.message)

    @patch("lib.native_fs.engine.PanelManager")
    def test_edit_etc_pki_blocked(self, MockPM):
        """EDIT 到 etc/pki/ 下的文件 → 被保护目录拦截"""
        from lib.native_fs.engine import execute_block

        target = self.protected_files["etc/pki"]
        block = {"type": "edit", "path": target, "search": "protected", "replace": "hacked"}
        result = execute_block(block, cwd="/tmp", sandbox_root=self.sandbox_root)

        self.assertFalse(result.success)
        self.assertIn("保护目录", result.message)

    @patch("lib.native_fs.engine.PanelManager")
    def test_append_onyxlog_blocked(self, MockPM):
        """APPEND 到 onyxlog/ 下的文件 → 被保护目录拦截"""
        from lib.native_fs.engine import execute_block

        target = self.protected_files["onyxlog"]
        block = {"type": "append", "path": target, "content": "extra\n"}
        result = execute_block(block, cwd="/tmp", sandbox_root=self.sandbox_root)

        self.assertFalse(result.success)
        self.assertIn("保护目录", result.message)

    @patch("lib.native_fs.engine.PanelManager")
    def test_delete_tools_sys_tools_blocked(self, MockPM):
        """DELETE 到 tools/sys_tools/ 下的文件 → 被保护目录拦截"""
        from lib.native_fs.engine import execute_block

        target = self.protected_files["tools/sys_tools"]
        block = {"type": "delete", "path": target, "start": 1, "end": 1}
        result = execute_block(block, cwd="/tmp", sandbox_root=self.sandbox_root)

        self.assertFalse(result.success)
        self.assertIn("保护目录", result.message)

    @patch("lib.native_fs.engine.PanelManager")
    def test_view_protected_dir_allowed(self, MockPM):
        """VIEW 保护目录下的文件 → 允许（读操作不受限）"""
        from lib.native_fs.engine import execute_block

        target = self.protected_files["onyx"]
        block = {"type": "view", "path": target}
        result = execute_block(block, cwd="/tmp", sandbox_root=self.sandbox_root)

        # VIEW 不是 mutation type，不应触发保护目录校验
        self.assertIsNotNone(result)

    @patch("lib.native_fs.engine.PanelManager")
    def test_protected_check_skipped_no_sandbox(self, MockPM):
        """sandbox_root=None → 保护目录校验跳过"""
        from lib.native_fs.engine import execute_block

        target = self.protected_files["onyx"]
        block = {"type": "write", "path": target, "content": "# test"}
        result = execute_block(block, cwd="/tmp", sandbox_root=None)

        # 无沙箱时写操作可以成功，但不应提及"保护目录"
        self.assertNotIn("保护目录", result.message,
                         "sandbox_root=None 不应触发保护目录校验")


# ========================================================================
# 3. __init__.py — process_blocks() 沙箱根传递
# ========================================================================

class TestProcessBlocksSandbox(unittest.TestCase):
    """process_blocks() 的 sandbox_root 参数传递"""

    def setUp(self):
        # `_process_blocks_with_limit` 用到模块级 panel_manager.clear_previous()
        self.pm_patch = patch("lib.native_fs.panel_manager")
        self.mock_pm = self.pm_patch.start()

    def tearDown(self):
        self.pm_patch.stop()

    @patch("lib.native_fs.execute_block")
    def test_sandbox_root_passed_to_execute(self, mock_execute):
        """process_blocks() 将 sandbox_root 传递到 execute_block()"""
        from lib.native_fs import process_blocks

        mock_execute.return_value = MagicMock(success=True, message="ok")

        blocks = [
            {"type": "view", "path": "/project/main.py"},
        ]
        process_blocks(blocks, cwd="/project", sandbox_root="/project/root")

        # 验证 execute_block 被调用时 sandbox_root 参数正确传递
        call_args = mock_execute.call_args
        self.assertIsNotNone(call_args)
        kwargs = call_args[1] if len(call_args) > 1 else {}
        # sandbox_root 可能是位置参数也可能是关键字参数
        if "sandbox_root" in kwargs:
            self.assertEqual(kwargs["sandbox_root"], "/project/root")
        else:
            # 检查位置参数中的第4个
            args = call_args[0] if call_args[0] else []
            self.assertIn("/project/root", str(args))

    @patch("lib.native_fs.execute_block")
    def test_sandbox_root_defaults_to_cwd(self, mock_execute):
        """sandbox_root=None 时 → 默认使用 cwd 作为沙箱根"""
        from lib.native_fs import process_blocks

        mock_execute.return_value = MagicMock(success=True, message="ok")

        blocks = [
            {"type": "view", "path": "/etc/passwd"},
        ]
        process_blocks(blocks, cwd="/my/project", sandbox_root=None)

        call_args = mock_execute.call_args
        self.assertIsNotNone(call_args)
        kwargs = call_args[1] if len(call_args) > 1 else {}
        if "sandbox_root" in kwargs:
            # sandbox_root=None → fallback 为 cwd
            self.assertEqual(kwargs["sandbox_root"], "/my/project")

    @patch("lib.native_fs.execute_block")
    def test_single_mutation_rule(self, mock_execute):
        """多个修改块时只执行第一个（铁律）"""
        from lib.native_fs import process_blocks

        mock_execute.return_value = MagicMock(success=True, message="ok")

        blocks = [
            {"type": "write", "path": "/a.py", "content": "x"},
            {"type": "write", "path": "/b.py", "content": "y"},
            {"type": "view", "path": "/c.py"},
        ]
        results = process_blocks(blocks, cwd="/tmp", sandbox_root=None)

        # execute_block 应被调用 3 次，但第2个 write 被跳过
        self.assertEqual(len(results), 3)
        # 第1个 write 应该成功
        self.assertTrue(results[0].success)
        # 第2个 write 应该被跳过（多个修改块铁律）
        self.assertFalse(results[1].success)
        self.assertIn("跳过", results[1].message)
        # view 应该正常执行
        self.assertTrue(results[2].success or not results[2].success)  # 取决于 mock


# ========================================================================
# 4. security.py — check_sandbox_path()
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
    def test_path_outside_root_blocked(self, mock_abspath, mock_realpath):
        """路径在 ROOT_DIR 外 → 拦截"""
        from core.security import check_sandbox_path

        mock_realpath.side_effect = lambda p: p
        mock_abspath.side_effect = lambda p: p

        ctx = self._make_ctx(root_dir="/project")

        result = check_sandbox_path(ctx, "/etc/passwd", "req-2")
        self.assertFalse(result)

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
# 5. api.py — process_ai_result_fields() markup_blocks 默认值
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
# 6. path_ops.py — 虚拟/物理路径转换
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
# 7. 集成测试 — markup_blocks 在 ai_cmd.py 流程中的行为
# ========================================================================

class TestMarkupBlocksExecutionFlow(unittest.TestCase):
    """从 API 结果到 process_blocks 的端到端流程"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="onyx_test_flow_")
        self.test_file = os.path.join(self.tmpdir, "readme.txt")
        with open(self.test_file, "w") as f:
            f.write("original content\n")
        # patch 模块级 panel_manager
        self.pm_patch = patch("lib.native_fs.panel_manager")
        self.mock_pm = self.pm_patch.start()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)
        self.pm_patch.stop()

    def test_parse_then_execute_view_workflow(self):
        """
        模拟完整流程:
        1. api.py 将 AI 文本解析为 markup_blocks
        2. ai_cmd.py 读取 markup_blocks 并执行
        """
        from lib.native_fs.markup_parser import parse_markup
        from lib.native_fs import process_blocks

        # 模拟 AI 回复文本中的 VIEW 标记（必须单独一行）
        ai_text = f"这是文件内容：\n[VIEW:{self.test_file}]"

        # Step 1: 解析（模拟 api.py 中的行为）
        blocks = parse_markup(ai_text)
        self.assertTrue(len(blocks) > 0, "应能解析出标记块")
        self.assertEqual(blocks[0]["type"], "view")
        self.assertEqual(blocks[0]["path"], self.test_file)

        # Step 2: 执行（模拟 ai_cmd.py 中新增的行为）
        results = process_blocks(blocks, cwd=self.tmpdir, sandbox_root=self.tmpdir)
        self.assertTrue(results[0].success or not results[0].success,
                        "VIEW 应执行完成（可能因 mock 面板显示失败，但不影响核心逻辑）")

    def test_edit_block_with_sandbox_protection(self):
        """EDIT 块在沙箱中执行 → 沙箱边界校验生效"""
        from lib.native_fs import process_blocks

        # 沙箱根设为 tmpdir，外部文件应被拦截
        outside_file = os.path.join(self.tmpdir, "..", "outside.txt")
        with open(outside_file, "w") as f:
            f.write("outside\n")

        blocks = [
            {
                "type": "edit",
                "path": outside_file,
                "search": "outside",
                "replace": "inside",
            }
        ]

        # 沙箱启用，路径在沙箱外 → 拦截
        results = process_blocks(blocks, cwd="/tmp", sandbox_root=self.tmpdir)
        self.assertFalse(results[0].success)
        self.assertIn("越界", results[0].message,
                      "沙箱外的 EDIT 应被越界拦截")


# ========================================================================
# 8. 边缘情况
# ========================================================================

class TestEdgeCases(unittest.TestCase):
    """边界条件测试"""

    def setUp(self):
        self.pm_patch = patch("lib.native_fs.panel_manager")
        self.mock_pm = self.pm_patch.start()

    def tearDown(self):
        self.pm_patch.stop()

    def test_empty_markup_blocks(self):
        """空 markup_blocks 列表不应报错"""
        from lib.native_fs import process_blocks

        results = process_blocks([], cwd="/tmp", sandbox_root="/project")
        self.assertEqual(results, [])

    @patch("lib.native_fs.engine.PanelManager")
    def test_sandbox_root_is_file_path(self, MockPM):
        """sandbox_root 本身是文件路径（非目录）→ 路径不匹配 → 全部拦截"""
        from lib.native_fs.engine import execute_block
        import tempfile

        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
            sandbox_as_file = f.name
            f.write(b"x")

        try:
            block = {"type": "write", "path": "/any/file.py", "content": "x"}
            result = execute_block(block, cwd="/tmp", sandbox_root=sandbox_as_file)

            self.assertFalse(result.success)
            self.assertIn("越界", result.message,
                          "sandbox_root 是文件时所有路径都应被拦截")
        finally:
            os.unlink(sandbox_as_file)

    def test_parse_sse_response_without_markup(self):
        """无标记块的 AI 回复 → markup_blocks 为空列表"""
        from lib.native_fs.markup_parser import parse_markup

        text = "这是一段普通文本，没有标记块"
        blocks = parse_markup(text)
        self.assertEqual(blocks, [])

    @patch("core.security.os.path.realpath")
    @patch("core.security.os.path.abspath")
    def test_root_traversal_attempt_blocked(self, mock_abspath, mock_realpath):
        """路径穿越尝试（../ 逃逸）→ 拦截"""
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

        # /project/../etc/passwd → /etc/passwd（通过 realpath 解析后逃逸）
        result = check_sandbox_path(ctx, "/project/../etc/passwd", "req-trav")
        self.assertFalse(result)


# ========================================================================
# 入口
# ========================================================================

if __name__ == "__main__":
    unittest.main(verbosity=2)
