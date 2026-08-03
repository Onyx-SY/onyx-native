# -*- coding: utf-8 -*-
"""
test_env_info.py — env_info 去 cwd 单元测试

覆盖：
  1. env_info 不再包含 '#Working directory' 与 cwd 路径
  2. env_info 标签中英双语（#任务 / #Task 等）
  3. 任务内容仍保留

运行:
  python -m pytest test/virtual/test_env_info.py -v
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

_ONYX_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ONYX_DIR not in sys.path:
    sys.path.insert(0, _ONYX_DIR)

import bin.ai_lib.api as api  # noqa: E402


class TestEnvInfoNoCwd(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="onyx_test_env_info_")
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = self.home
        self.captured = {}
        self.fake_conf = {"api_key": "k" * 32, "platform": "deepseek",
                          "model": "deepseek-chat", "params": {}}

    def tearDown(self):
        import shutil
        shutil.rmtree(self.home)
        if self._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._old_home

    def _fake_post(self, url, headers=None, json=None, timeout=None, stream=None):
        self.captured["payload"] = json
        resp = MagicMock()
        resp.status_code = 200
        resp.iter_lines.return_value = iter([])
        resp.raise_for_status = lambda: None
        return resp

    def _run(self):
        with patch("bin.ai_lib.config.load_key_conf", return_value=self.fake_conf), \
             patch.object(api.requests, "post", side_effect=self._fake_post):
            api.call_ai_api_sse(question="hello test", user_home_dir=self.home,
                                tools=[], session_id="test-env-info")
        msgs = self.captured["payload"]["messages"]
        return msgs[-1]["content"]

    def test_no_working_directory(self):
        """env_info 中不再有 Working directory / cwd"""
        content = self._run()
        self.assertNotIn("Working directory", content)
        self.assertNotIn("工作目录", content)

    def test_no_cwd_path(self):
        """env_info 中不包含当前工作目录路径"""
        content = self._run()
        self.assertNotIn(os.getcwd(), content)

    def test_task_label_bilingual(self):
        """任务标签为双语：#任务 / #Task"""
        content = self._run()
        self.assertIn("#任务", content)
        self.assertIn("#Task", content)

    def test_task_question_present(self):
        """用户问题仍保留在 env_info 尾部"""
        content = self._run()
        self.assertIn("hello test", content)

    def test_env_labels_bilingual(self):
        """System / 用户 等标签为双语"""
        content = self._run()
        self.assertIn("系统 / System", content)
        self.assertIn("用户 / User", content)


if __name__ == "__main__":
    unittest.main()
