#!/usr/bin/env python3
"""home 豁免 + 链式语义 + 会话记忆 单元测试（离线）。

用户决策（2026-08）：
- cwd 与操作路径全部位于用户主目录内 → 跳过细颗粒+高级语法审查（home 内
  `mkdir && echo` 直接执行）；
- 附带最简单语义：链式命令（; && || |）子命令头命中 home 黑名单（rm/rmdir/dd…）
  仍然拦截——只过滤最简单攻击，深层对抗不在范围；
- low/mid 人类确认复用会话级记忆：本会话确认过一次后，后续直接执行。

运行: python3 test/virtual/test_fine_grained_home.py -v
"""
import builtins
import os
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from lib import safe  # noqa: E402
from lib.safe import check_fine_grained_advanced_syntax, check_path_permission_for_cmd  # noqa: E402


def _low_mode():
    return types.SimpleNamespace(current_mode="low")


class TestFineGrainedHome(unittest.TestCase):
    """home 豁免 + 链式语义"""

    def setUp(self):
        self._old = (safe.PERM_PATH_CONFIG, safe.PERM_PATH_CONFIG_LOADED,
                     safe._ROOT_DIR, safe._SESSION_CAPTCHA_VERIFIED)
        safe._SESSION_CAPTCHA_VERIFIED = False
        self._tmp = tempfile.mkdtemp(prefix="onyx_fg_")
        self.root = os.path.realpath(self._tmp)
        self.home = os.path.join(self.root, "home", "user")
        os.makedirs(self.home, exist_ok=True)
        safe._ROOT_DIR = self.root
        safe.PERM_PATH_CONFIG_LOADED = False
        safe.PERM_PATH_CONFIG = []
        from lib.safe import load_perm_path_config
        load_perm_path_config(self.root, "user", None)

    def tearDown(self):
        (safe.PERM_PATH_CONFIG, safe.PERM_PATH_CONFIG_LOADED,
         safe._ROOT_DIR, safe._SESSION_CAPTCHA_VERIFIED) = self._old
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _check(self, paths, advanced, cmd_str, mode=None):
        return check_fine_grained_advanced_syntax(
            paths, self.root, "user", mode or _low_mode(), advanced,
            log_error_func=None, request_id="t", user_home=self.home,
            cmd_str=cmd_str)

    # ── 用户场景：home 内安全链式直接放行 ──

    def test_home_benign_chain_allowed(self):
        adv = {"has_logical_operators": True}
        ok = self._check([self.home, os.path.join(self.home, "gomoku")],
                         adv, "mkdir -p gomoku/gomoku gomoku/templates && echo OK")
        self.assertTrue(ok, "home 内 mkdir && echo 应直接放行，无确认")

    def test_home_single_command_no_advanced_allowed(self):
        ok = self._check([self.home], {}, "mkdir -p gomoku")
        self.assertTrue(ok)

    # ── 最简单语义：链式里的黑名单命令仍拦截 ──

    def test_home_chain_with_rm_blocked(self):
        ok = self._check([self.home], {"has_logical_operators": True},
                         "ls && rm -rf x")
        self.assertFalse(ok, "链式含 rm 应拦截")

    def test_home_semicolon_chain_rm_blocked(self):
        ok = self._check([self.home], {}, "mkdir x; rm -rf y")
        self.assertFalse(ok, "; 链含 rm 应拦截（补上既有洞）")

    def test_home_heredoc_body_not_treated_as_command(self):
        """复现 2026-09 实测：`cd gomoku && cat > x.py << 'EOF'` 的 heredoc 正文里
        出现 rm 被误判为命令头 → 误拦截。修复后正文剔除，只检查真实命令链 → 放行。"""
        with mock.patch("os.getcwd", return_value=self.home):
            ok = self._check([self.home], {"has_logical_operators": True},
                             "cd gomoku && cat > smoke_auth.py << 'EOF'\n"
                             "import os\n"
                             "os.remove('x')\n"
                             "rm -f /tmp/smoke_auth.tmp\n"
                             "EOF")
        self.assertTrue(ok, "heredoc 正文里的 rm 不应被当作命令头拦截")

    def test_home_heredoc_body_rm_but_chain_has_real_rm(self):
        """反例：链上真有 rm（heredoc 之外）→ 仍拦截，防线不因剥离而失效。"""
        with mock.patch("os.getcwd", return_value=self.home):
            ok = self._check([self.home], {"has_logical_operators": True},
                             "echo a << 'E'\nrm x\nE\n&& rm -rf y")
        self.assertFalse(ok, "heredoc 外的真实 rm 链仍应拦截")

    def test_home_here_string_not_heredoc(self):
        """`<<<`（here-string）不是 heredoc 块，不应触发剥离逻辑、也不误判。"""
        with mock.patch("os.getcwd", return_value=self.home):
            ok = self._check([self.home], {"has_logical_operators": True},
                             "cat <<< 'rm x' && echo ok")
        self.assertTrue(ok, "here-string 中的 rm 文本不是命令，应放行")

    def test_home_pipeline_rm_blocked(self):
        ok = self._check([self.home], {"has_pipeline": True},
                         "cat a | dd of=b")
        self.assertFalse(ok, "管道含 dd 应拦截")

    def test_home_plain_rm_allowed(self):
        """2026-09 用户决策：cwd 与目标均在 home → 路径权限检查直接放行
        （rm 不再被 /home/<username> 黑名单拦截/确认）。"""
        allowed = check_path_permission_for_cmd(
            "rm", [self.home], "user", _low_mode(),
            log_error_func=None, request_id="t", user_home=self.home)
        self.assertTrue(allowed, "home 内非链式 rm 应直接放行（home 豁免）")

    def test_home_exempt_requires_user_home_real_dir(self):
        """前提校验：user_home 必须 realpath 后是真实存在的目录，否则不豁免。"""
        fake_home = os.path.join(self.root, "home", "nonexistent_user")
        allowed = check_path_permission_for_cmd(
            "rm", [self.home], "user", _low_mode(),
            log_error_func=None, request_id="t", user_home=fake_home)
        self.assertFalse(allowed, "user_home 不存在时不豁免，仍走黑名单拦截")

    def test_home_exempt_requires_all_paths_in_home(self):
        """cwd 在 home 外、目标在 home → 不全在 home 内 → 不豁免。"""
        os.makedirs(os.path.join(self.root, "etc"), exist_ok=True)
        with open(os.path.join(self.root, "etc", "x"), "w") as f:
            f.write("x")
        mixed = [self.home, os.path.join(self.root, "etc", "x")]
        allowed = check_path_permission_for_cmd(
            "rm", mixed, "user", _low_mode(),
            log_error_func=None, request_id="t", user_home=self.home)
        self.assertFalse(allowed, "含 home 外路径时不豁免，仍走规则拦截")

    def test_home_exempt_with_relative_paths(self):
        """复现 2026-09 实测：resolve 对裸相对路径原样返回，路径列表混入
        相对路径（`rm -f gomoku/c_core/_test_train.so` + cwd）→ 按 cwd
        绝对化后判定，应豁免放行不再弹确认。"""
        os.makedirs(os.path.join(self.home, "gomoku", "c_core"), exist_ok=True)
        with open(os.path.join(self.home, "gomoku", "c_core", "_test_train.so"), "w") as f:
            f.write("x")
        with mock.patch("os.getcwd", return_value=self.home):
            allowed = check_path_permission_for_cmd(
                "rm", ["gomoku/c_core/_test_train.so", self.home], "user", _low_mode(),
                log_error_func=None, request_id="t", user_home=self.home)
        self.assertTrue(allowed, "相对路径按 cwd 绝对化后应在 home 内 → 豁免放行")

    def test_home_exempt_relative_path_escaping_home(self):
        """反例：cwd 在 home，但相对路径含 .. 解析出 home（../etc/x）→ 不豁免。"""
        os.makedirs(os.path.join(self.root, "etc"), exist_ok=True)
        with mock.patch("os.getcwd", return_value=self.home):
            allowed = check_path_permission_for_cmd(
                "rm", ["../etc/x", self.home], "user", _low_mode(),
                log_error_func=None, request_id="t", user_home=self.home)
        self.assertFalse(allowed, "相对路径逃出 home 时不豁免")

    def test_root_exempt_only_for_root_user(self):
        """/root 豁免前提：仅 username=root 时视为自己的主目录。"""
        root_dir = os.path.join(self.root, "root")
        os.makedirs(root_dir, exist_ok=True)
        paths = [root_dir]
        # root 用户 → 豁免
        ok_root = check_path_permission_for_cmd(
            "rm", paths, "root", _low_mode(),
            log_error_func=None, request_id="t", user_home=self.home)
        self.assertTrue(ok_root, "username=root 时 /root 应豁免")
        # 非 root 用户 → 不豁免
        ok_user = check_path_permission_for_cmd(
            "rm", paths, "user", _low_mode(),
            log_error_func=None, request_id="t", user_home=self.home)
        self.assertFalse(ok_user, "非 root 用户的 /root 不豁免，仍走规则拦截")

    # ── home 外路径不受影响 ──

    def test_etc_rule_still_enforced(self):
        adv = {"has_pipeline": True}
        os.makedirs(os.path.join(self.root, "etc"), exist_ok=True)
        with open(os.path.join(self.root, "etc", "x"), "w") as f:
            f.write("x")
        ok = self._check([os.path.join(self.root, "etc", "x")],
                         adv, "cat /etc/x | head", mode=_low_mode())
        self.assertFalse(ok, "/etc 白名单 + 管道仍应拦截")


class TestNonAdvSessionRemember(unittest.TestCase):
    """low/mid 人类确认复用会话级记忆：确认一次后后续直接执行"""

    def setUp(self):
        self._old = (safe.PERM_PATH_CONFIG, safe.PERM_PATH_CONFIG_LOADED,
                     safe._ROOT_DIR, safe._SESSION_CAPTCHA_VERIFIED)
        safe._SESSION_CAPTCHA_VERIFIED = False
        self._tmp = tempfile.mkdtemp(prefix="onyx_fg_")
        self.root = os.path.realpath(self._tmp)
        self.home = os.path.join(self.root, "home", "user")
        os.makedirs(self.home, exist_ok=True)
        # 注入自定义规则：/testzone 黑名单、min_mode=low、允许高级语法（触发确认分支）
        safe.PERM_PATH_CONFIG = [{
            "global_match": False,
            "fixed_phys": os.path.join(self.root, "testzone"),
            "fixed_virt": "/testzone",
            "name_pattern": "*",
            "depth": 10,
            "raw_pattern": "/testzone/<*:10>",
            "mode": "blacklist",
            "allow_advanced_syntax": True,
            "min_mode": "low",
            "allowed": ["rm"],
        }]
        safe.PERM_PATH_CONFIG_LOADED = True
        safe._ROOT_DIR = self.root
        os.makedirs(os.path.join(self.root, "testzone"), exist_ok=True)
        with open(os.path.join(self.root, "testzone", "x"), "w") as f:
            f.write("x")

    def tearDown(self):
        (safe.PERM_PATH_CONFIG, safe.PERM_PATH_CONFIG_LOADED,
         safe._ROOT_DIR, safe._SESSION_CAPTCHA_VERIFIED) = self._old
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_confirm_once_then_skip(self):
        paths = [os.path.join(self.root, "testzone", "x")]
        adv = {"has_logical_operators": True}
        kwargs = dict(log_error_func=None, request_id="t", user_home=self.home,
                      cmd_str="echo a && echo b")
        with mock.patch("builtins.input", return_value="y") as m_in:
            ok1 = check_fine_grained_advanced_syntax(
                paths, self.root, "user", _low_mode(), adv, **kwargs)
        self.assertTrue(ok1, "第一次确认 y 应放行")
        # 第二次：会话已确认过 → 不再弹输入，直接放行
        with mock.patch("builtins.input", side_effect=AssertionError("不应再次询问")) as m_in2:
            ok2 = check_fine_grained_advanced_syntax(
                paths, self.root, "user", _low_mode(), adv, **kwargs)
        self.assertTrue(ok2, "会话确认过一次后应直接放行")

    def test_cancel_returns_false(self):
        paths = [os.path.join(self.root, "testzone", "x")]
        adv = {"has_logical_operators": True}
        with mock.patch("builtins.input", return_value="n"):
            ok = check_fine_grained_advanced_syntax(
                paths, self.root, "user", _low_mode(), adv,
                log_error_func=None, request_id="t", user_home=self.home,
                cmd_str="echo a && echo b")
        self.assertFalse(ok, "取消应拦截")


if __name__ == "__main__":
    unittest.main()
