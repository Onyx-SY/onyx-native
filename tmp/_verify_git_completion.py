#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 git 补全：直读 .git 优先 + CLI 兜底。

在真实仓库 tmp/gitdemo（分支 dev/main/master，标签 v1.0/v2.0，
remote origin/upstream）上跑端到端断言。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DEMO = os.path.join(ROOT, "tmp", "gitdemo")
os.chdir(DEMO)

from lib.terminal.cmd_com import builtin as B           # noqa: E402
from lib.terminal.dynamic_cmd import CompletionContext   # noqa: E402
import lib.terminal.dynamic_cmd as D                     # noqa: E402

FAILS = []


def items(args, current):
    ctx = CompletionContext(cmd="git", args=args, current=current, cwd=os.getcwd())
    return [(i.text, i.meta) for i in B.git_completer(ctx)]


def check(name, got, want):
    ok = got == want
    print(("  ✅ " if ok else "  ❌ ") + name)
    print("     got : %r" % (got,))
    if not ok:
        print("     want: %r" % (want,))
        FAILS.append(name)


def clear_cache():
    with D._cache_lock:
        D._cache.clear()


print("[1] 直读 .git")
check("_find_git_dir", os.path.realpath(B._find_git_dir()),
      os.path.realpath(os.path.join(DEMO, ".git")))
check("_read_git_refs(heads)", B._read_git_refs("heads"), ["dev", "main", "master"])
check("_read_git_refs(tags)", B._read_git_refs("tags"), ["v1.0", "v2.0"])
check("_read_git_remotes", sorted(B._read_git_remotes()), ["origin", "upstream"])

print("[2] git checkout <TAB> → 分支")
clear_cache()
check("git checkout ''", sorted(t for t, m in items(["checkout"], "") if m == "branch"),
      ["dev", "main", "master"])

print("[3] git checkout v<TAB> → 标签")
clear_cache()
check("git checkout 'v'", sorted(t for t, m in items(["checkout"], "v") if m == "tag"),
      ["v1.0", "v2.0"])

print("[4] git switch <TAB> → 分支")
clear_cache()
check("git switch ''", sorted(t for t, m in items(["switch"], "") if m == "branch"),
      ["dev", "main", "master"])

print("[5] git push <TAB> → remote")
clear_cache()
check("git push ''", sorted(t for t, m in items(["push"], "") if m == "remote"),
      ["origin", "upstream"])

print("[6] git push origin <TAB> → 分支")
clear_cache()
check("git push origin ''", sorted(t for t, m in items(["push", "origin"], "") if m == "branch"),
      ["dev", "main", "master"])

print("[7] git remote <TAB> → 子命令")
clear_cache()
check("git remote ''", sorted(t for t, m in items(["remote"], "") if m == "action"),
      sorted(["add", "remove", "rename", "set-url", "show", "prune", "get-url", "set-head"]))

print("[8] git CLI 不可用（打桩 run_command → []）时仍能补出分支/标签/remote")
clear_cache()
_real_run = B.run_command
B.run_command = lambda *a, **k: []
try:
    check("checkout (no CLI)", sorted(t for t, m in items(["checkout"], "") if m == "branch"),
          ["dev", "main", "master"])
    clear_cache()
    check("tags (no CLI)", sorted(t for t, m in items(["checkout"], "v") if m == "tag"),
          ["v1.0", "v2.0"])
    clear_cache()
    check("push remote (no CLI)", sorted(t for t, m in items(["push"], "") if m == "remote"),
          ["origin", "upstream"])
finally:
    B.run_command = _real_run
    clear_cache()

print("[9] 非仓库目录 → 不崩、返回空")
os.chdir("/")
clear_cache()
check("git checkout '' @ /", sorted(t for t, m in items(["checkout"], "") if m == "branch"), [])
os.chdir(DEMO)

print()
if FAILS:
    print("❌ FAILED: %d 项 -> %s" % (len(FAILS), FAILS))
    sys.exit(1)
print("✅ ALL PASS")
