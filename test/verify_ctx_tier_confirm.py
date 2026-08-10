"""Smoke test: 3-tier dangerous command confirmation (2026-09)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from unittest.mock import patch
from bin.ai_lib import helpers

LANG = {"danger_cmd_title": "t", "danger_cmd_display": "cmd",
        "danger_cmd_msg": "risk: {0}", "danger_cmd_executing": "exec",
        "danger_cmd_cancelled": "cancel", "danger_cmd_reason_recorded": "rec"}

# 1. ctx < 300k: auto-allow, UI never called
with patch.object(helpers, "ui_confirm_dangerous", side_effect=AssertionError("UI must not be called")) as ui:
    ok, resp, reason = helpers.confirm_dangerous_command(
        "rm -rf tmp", "rm", LANG, "s1", "", 0, context_tokens=100_000)
    assert (ok, resp) == (True, "auto"), (ok, resp)
    ui.assert_not_called()
    print("PASS: ctx<300k auto-allow (trust zone), no UI")

# 2. 300k <= ctx <= 600k: UI called with timeout=10, timeout_default=True
with patch.object(helpers, "ui_confirm_dangerous", return_value=(True, "timeout", "")) as ui:
    ok, resp, reason = helpers.confirm_dangerous_command(
        "rm -rf tmp", "rm", LANG, "s1", "", 0, context_tokens=400_000)
    assert ok is True, ok
    kwargs = ui.call_args.kwargs
    assert kwargs["timeout"] == 10 and kwargs["timeout_default"] is True, kwargs
    print("PASS: 300k<=ctx<=600k soft-confirm (timeout=10, default ALLOW)")

# 3. ctx > 600k: UI called with timeout=None (force answer)
with patch.object(helpers, "ui_confirm_dangerous", return_value=(False, "n", "用户拒绝")) as ui:
    ok, resp, reason = helpers.confirm_dangerous_command(
        "rm -rf tmp", "rm", LANG, "s1", "", 0, context_tokens=700_000)
    assert ok is False and resp == "n", (ok, resp)
    kwargs = ui.call_args.kwargs
    assert kwargs["timeout"] is None, kwargs
    print("PASS: ctx>600k force-confirm (timeout=None, must answer)")

# 4. Estimation failure (ctx=0) -> treated as force tier
with patch.object(helpers, "ui_confirm_dangerous", return_value=(False, "n", "拒绝")) as ui:
    ok, resp, _ = helpers.confirm_dangerous_command(
        "rm -rf tmp", "rm", LANG, "s1", "", 0, context_tokens=0)
    assert ok is False, ok
    assert ui.call_args.kwargs["timeout"] is None
    print("PASS: estimation failure (0) -> force tier (safe direction)")

# 5. extra_dangerous at trust zone: uniform 3-tier (no UI at <300k)
with patch.object(helpers, "ui_confirm_dangerous", side_effect=AssertionError("UI must not be called")):
    ok, resp, _ = helpers.confirm_dangerous_command(
        "rm -rf /", "rm", LANG, "s1", "", 0, context_tokens=50_000, extra_dangerous=True)
    assert ok is True, ok
    print("PASS: extra-dangerous also follows trust zone (<300k no UI)")

print("ALL_OK")
