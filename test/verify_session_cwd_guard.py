"""Smoke test: AI session cwd guard (2026-09 fix for cd-stuck-at-root)."""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from bin.ai_lib import sandbox

# 1. guard restores cwd after AI-session cd (simulated via os.chdir)
with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
    os.chdir(d1)
    before = os.getcwd()
    sandbox.deactivate()
    with sandbox.session_guard():
        sandbox.init(cwd=d1)
        os.chdir(d2)          # simulate AI's in-process cd (e.g. cd /)
        assert sandbox.is_active()
        assert os.getcwd() == d2
    # after exit: cwd restored AND sandbox deactivated
    assert os.getcwd() == before, f"cwd not restored: {os.getcwd()} != {before}"
    assert not sandbox.is_active(), "sandbox must be deactivated after session"
    print("PASS: session_guard restores cwd + deactivates sandbox on exit")

# 2. guard restores on exception too
with tempfile.TemporaryDirectory() as d3:
    os.chdir(d3)
    try:
        with sandbox.session_guard():
            os.chdir("/")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert os.getcwd() == d3, f"cwd not restored on exception: {os.getcwd()}"
    assert not sandbox.is_active()
    print("PASS: session_guard restores cwd on exception")

# 3. guard no-ops when cwd unchanged
with tempfile.TemporaryDirectory() as d4:
    os.chdir(d4)
    with sandbox.session_guard():
        pass
    assert os.getcwd() == d4
    print("PASS: session_guard no-op when cwd unchanged")

print("ALL_OK")
