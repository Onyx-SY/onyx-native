"""Smoke test: session-level captcha skip (2026-09 change)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from lib.safe import (
    _SESSION_CAPTCHA_VERIFIED, is_session_captcha_verified,
    mark_session_captcha_verified, _adv_confirm_with_disable_option,
)

# 1. Initial state
assert is_session_captcha_verified() is False, "flag must start False"
print("PASS: initial flag False")

# 2. Skip path: session verified -> returns True WITHOUT consuming input
mark_session_captcha_verified()
assert is_session_captcha_verified() is True, "flag must be True after mark"

def _boom(*a, **k):
    raise AssertionError("input() must NOT be called when session already verified")

orig_input = __builtins__.input
__builtins__.input = _boom
try:
    ok = _adv_confirm_with_disable_option("warn", "confirm", "path")
    assert ok is True, f"expected True when session verified, got {ok}"
    print("PASS: skip path returns True, no input() call")
finally:
    __builtins__.input = orig_input

# 3. Reset flag for next test
import lib.safe as _safe
_safe._SESSION_CAPTCHA_VERIFIED = False

# 4. Not verified -> input() IS called (EOFError -> False)
def _eof(*a, **k):
    raise EOFError

__builtins__.input = _eof
try:
    ok = _adv_confirm_with_disable_option("warn", "confirm", "path")
    assert ok is False, "expected False on EOF/cancel"
    print("PASS: unverified path still prompts (EOF cancels -> False)")
finally:
    __builtins__.input = orig_input

print("ALL_OK")
