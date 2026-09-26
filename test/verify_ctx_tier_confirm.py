"""Smoke test: 3-tier dangerous command confirmation (2026-09, fail-closed).

规则（与 helpers.confirm_dangerous_command 文档一致）：
  - ctx < 300k          → 信任窗口，直接放行（不弹 UI）
  - 300k ≤ ctx ≤ 600k   → 弹窗，10s 无操作默认**拒绝**（timeout_default=False）
  - ctx > 600k          → 强制人工回答（timeout=None）
  - ctx ≤ 0（估算失败） → 按强制档处理（安全方向）
  - lang_text 缺键/空   → 回退完整词表/内置默认，绝不 KeyError

运行: python3 test/verify_ctx_tier_confirm.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from unittest.mock import patch
from bin.ai_lib import helpers

LANG = {"danger_cmd_title": "t", "danger_cmd_display": "cmd",
        "danger_cmd_msg": "risk: {0}", "danger_cmd_executing": "exec",
        "danger_cmd_cancelled": "cancel", "danger_cmd_reason_recorded": "rec"}

# 环境隔离：确保走默认分级策略（不受外部 env 影响）
os.environ.pop("ONYX_DANGER_AUTO_ALLOW", None)
os.environ.pop("ONYX_DANGER_TRUST_TOKENS", None)

# 1. ctx < 300k: auto-allow, UI never called
with patch.object(helpers, "ui_confirm_dangerous", side_effect=AssertionError("UI must not be called")) as ui:
    ok, resp, reason = helpers.confirm_dangerous_command(
        "rm -rf tmp", "rm", LANG, "s1", "", 0, context_tokens=100_000)
    assert (ok, resp) == (True, "auto"), (ok, resp)
    ui.assert_not_called()
    print("PASS: ctx<300k auto-allow (trust zone), no UI")

# 2. 300k <= ctx <= 600k: UI called with timeout=10, timeout_default=False (fail-closed)
with patch.object(helpers, "ui_confirm_dangerous", return_value=(False, "timeout", "")) as ui:
    ok, resp, reason = helpers.confirm_dangerous_command(
        "rm -rf tmp", "rm", LANG, "s1", "", 0, context_tokens=400_000)
    kwargs = ui.call_args.kwargs
    assert kwargs["timeout"] == 10, kwargs
    assert kwargs["timeout_default"] is False, kwargs      # 超时 = 拒绝（旧实现是 True）
    assert ok is False and resp == "n", (ok, resp)         # 超时未确认 → 不放行
    print("PASS: 300k<=ctx<=600k soft-confirm (timeout=10, default DENY)")

# 3. ctx > 600k: UI called with timeout=None (force answer)
with patch.object(helpers, "ui_confirm_dangerous", return_value=(False, "n", "用户拒绝")) as ui:
    ok, resp, reason = helpers.confirm_dangerous_command(
        "rm -rf tmp", "rm", LANG, "s1", "", 0, context_tokens=700_000)
    assert ok is False and resp == "n", (ok, resp)
    assert ui.call_args.kwargs["timeout"] is None, ui.call_args.kwargs
    print("PASS: ctx>600k force-confirm (timeout=None, must answer)")

# 4. Estimation failure (ctx=0) -> treated as force tier
with patch.object(helpers, "ui_confirm_dangerous", return_value=(False, "n", "拒绝")) as ui:
    ok, resp, _ = helpers.confirm_dangerous_command(
        "rm -rf tmp", "rm", LANG, "s1", "", 0, context_tokens=0)
    assert ok is False, ok
    assert ui.call_args.kwargs["timeout"] is None, ui.call_args.kwargs
    print("PASS: estimation failure (0) -> force tier (safe direction)")

# 5. extra_dangerous at trust zone: uniform 3-tier (no UI at <300k)
with patch.object(helpers, "ui_confirm_dangerous", side_effect=AssertionError("UI must not be called")):
    ok, resp, _ = helpers.confirm_dangerous_command(
        "rm -rf /", "rm", LANG, "s1", "", 0, context_tokens=50_000, extra_dangerous=True)
    assert ok is True, ok
    print("PASS: extra-dangerous also follows trust zone (<300k no UI)")

# 6. Empty lang_text -> no KeyError (fallback to full dict / builtin defaults)
with patch.object(helpers, "ui_confirm_dangerous", return_value=(False, "n", "")) as ui:
    ok, resp, _ = helpers.confirm_dangerous_command(
        "rm -rf tmp", "rm", {}, "s1", "", 0, context_tokens=400_000)
    assert ui.call_args.kwargs.get("title"), ui.call_args.kwargs
    assert ui.call_args.kwargs.get("command"), ui.call_args.kwargs
    print("PASS: empty lang_text -> no KeyError (fallback copy)")

print("ALL_OK")
