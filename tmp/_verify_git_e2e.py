#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端：在真实 git 仓库里验证 git checkout / push 的动态补全。"""
import os
import sys

ROOT = "/storage/emulated/0/abPython/PythonProject/工具/Hacker--V1.00.1/src/Hacker/onyx-test/home/u0_a305/onyx/onyx"
REPO = os.path.join(ROOT, "tmp", "gitdemo")
sys.path.insert(0, ROOT)

from prompt_toolkit.document import Document
from lib.terminal.com import SmartCompleter
from lib.terminal import dynamic_cmd

os.chdir(REPO)

sc = SmartCompleter(
    ["git"],
    cmd_config_path=os.path.join(ROOT, "etc", "cmd.json"),
    com_cmd_config_path=os.path.join(ROOT, "etc", "cmdal.json"),
    virtual_root=REPO,
    user_home_dir=os.path.expanduser("~"),
    history_buffer=[],
)

ok = True


def run(text):
    doc = Document(text, len(text))
    return [(c.text, str(c.display_meta)) for c in sc.get_completions(doc, None)]


print("cwd      :", os.getcwd())
print("has git  :", sc.dynamic_manager.has("git"))

# 1) git checkout <tab> → 分支 + 标签
items = run("git checkout ")
print("[1] 'git checkout '  ->", items)
texts = {t for t, _ in items}
ok &= {"dev", "main", "master"} <= texts
ok &= {"v1.0", "v2.0"} <= texts

# 2) git checkout v → 只剩标签
items = run("git checkout v")
print("[2] 'git checkout v' ->", items)
ok &= {t for t, _ in items} == {"v1.0", "v2.0"}

# 3) git push <tab> → remote
items = run("git push ")
print("[3] 'git push '      ->", items)
ok &= {t for t, _ in items} == {"origin", "upstream"}

# 4) 模拟 git CLI 完全不可用（run_command 恒返回 []）→ 仍应能补出分支
dyn_mod = next(m for n, m in list(sys.modules.items())
               if n.startswith("onyx_dyn_builtin_") and getattr(m, "git_completer", None))
dyn_mod.run_command = lambda *a, **k: []
dynamic_cmd._cache.clear()
items = run("git checkout ")
print("[4] CLI 不可用        ->", items)
ok &= {"dev", "main", "master"} <= {t for t, _ in items}

# 5) 不在仓库里 → 不炸、返回空/其他
os.chdir(ROOT)
dynamic_cmd._cache.clear()
items = run("git checkout ")
print("[5] 非仓库目录        ->", items)
ok &= all(m == "tag" or m == "branch" for _, m in items)

print()
print("VERIFY:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
