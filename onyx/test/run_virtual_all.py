#!/usr/bin/env python3
"""Run all virtual tests, print per-file results."""
import os, sys, subprocess

base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "virtual")
files = sorted(f for f in os.listdir(base) if f.startswith("test_") and f.endswith(".py"))

failed = []
for f in files:
    path = os.path.join(base, f)
    r = subprocess.run([sys.executable, "-m", "unittest", path],
                       capture_output=True, text=True)
    last = r.stdout.strip().splitlines()[-3:] if r.stdout.strip() else r.stderr.strip().splitlines()[-3:]
    status = "OK " if r.returncode == 0 else "FAIL"
    print(f"[{status}] {f}: {' | '.join(last)}")
    if r.returncode != 0:
        failed.append(f)

print(f"\n{'ALL_OK' if not failed else 'FAILED: ' + ', '.join(failed)}")
sys.exit(1 if failed else 0)
