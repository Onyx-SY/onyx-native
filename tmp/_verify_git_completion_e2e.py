#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端：走真实 SmartCompleter（含静态树 + 动态路由），验证
   `git checkout <TAB>` 在真实仓库里补出分支，而不是静态选项/字典。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DEMO = os.path.join(ROOT, "tmp", "gitdemo")
os.chdir(DEMO)

from prompt_toolkit.document import Document          # noqa: E402
from lib.terminal.com import SmartCompleter            # noqa: E402

FAILS = []


def run(text):
    c = SmartCompleter(
        cmd_list=["git", "ls", "cd", "echo"],
        cmd_config_path=os.path.join(ROOT, "etc", "cmd.json"),
        com_cmd_config_path=os.path.join(ROOT, "etc", "cmdal.json"),
        virtual_root=ROOT,
        user_home_dir=os.path.join(ROOT, "tmp", "_probe_home"),
    )
    doc = Document(text, len(text))
    out = list(c.get_completions(doc, None))

    def meta_str(m):
        if m is None:
            return ""
        try:
            return "".join(t for _s, t in m)     # prompt_toolkit FormattedText
        except Exception:
            return str(m)

    return [(x.text, meta_str(x.display_meta)) for x in out]


def check(name, got, want):
    ok = got == want
    print(("  ✅ " if ok else "  ❌ ") + name)
    print("     got : %r" % (got,))
    if not ok:
        print("     want: %r" % (want,))
        FAILS.append(name)


print("[e2e] SmartCompleter 全链路 @ %s" % DEMO)
got = run("git checkout ")
check("git checkout <TAB> 只有 branch",
      sorted(t for t, m in got if m == "branch"), ["dev", "main", "master"])
check("git checkout <TAB> 无静态噪声（仅 branch/tag）",
      sorted(set(m for _t, m in got)), ["branch", "tag"])
check("git checkout <TAB> 具体项",
      sorted(t for t, _m in got), ["dev", "main", "master", "v1.0", "v2.0"])

got = run("git checkout v")
check("git checkout v<TAB> → tag",
      sorted(t for t, m in got if m == "tag"), ["v1.0", "v2.0"])

got = run("git switch ")
check("git switch <TAB> → branch",
      sorted(t for t, m in got if m == "branch"), ["dev", "main", "master"])

got = run("git push ")
check("git push <TAB> → remote",
      sorted(t for t, m in got if m == "remote"), ["origin", "upstream"])

print()
if FAILS:
    print("❌ FAILED: %d -> %s" % (len(FAILS), FAILS))
    sys.exit(1)
print("✅ ALL PASS")
