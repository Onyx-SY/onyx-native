#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证：修复 default_script_dirs 后，git checkout <tab> 走动态分支补全。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from prompt_toolkit.document import Document
from lib.terminal.com import SmartCompleter

sc = SmartCompleter(
    ["git", "ls", "echo"],
    cmd_config_path=os.path.join(ROOT, "etc", "cmd.json"),
    com_cmd_config_path=os.path.join(ROOT, "etc", "cmdal.json"),
    virtual_root=ROOT,
    user_home_dir=os.path.expanduser("~"),
    history_buffer=[],
)

ok = True

# 1) 内置脚本被加载
loaded = sc.dynamic_manager.registry._scripts_loaded
has_git = sc.dynamic_manager.has("git")
print("[1] has('git') =", has_git)
print("    内置脚本:", [os.path.basename(p) for p in loaded])
ok &= has_git and any(p.endswith("builtin.py") for p in loaded)

# 2) 打桩分支列表（当前目录不是 git 仓库，无法真实取分支）
dyn_mod = None
for name, mod in list(sys.modules.items()):
    if name.startswith("onyx_dyn_builtin_") and getattr(mod, "git_completer", None):
        dyn_mod = mod
        break
assert dyn_mod is not None, "内置脚本未加载到 sys.modules"
dyn_mod._git_branches = lambda: ["main", "feature/login", "hotfix-1.0"]
dyn_mod._git_tags = lambda: ["v1.0.0"]

# 3) git checkout <tab> → 动态上下文 + branch 补全
for text in ["git checkout ", "git checkout fe"]:
    doc = Document(text, len(text))
    ctx = sc._get_context(doc)
    items = [(c.text, str(c.display_meta)) for c in sc.get_completions(doc, None)]
    metas = [m for _, m in items]
    print("[2] 输入 %-18r ctx=%-8s -> %s" % (text, ctx[0], items))
    ok &= ctx[0] == "dynamic"
    ok &= len(items) > 0
    ok &= all("branch" in m or "tag" in m for m in metas)
    ok &= any("branch" in m for m in metas)

# 4) 回归：非动态命令（git 之外）仍走静态树，不受影响
doc = Document("git ", 4)
items = [(c.text, str(c.display_meta)) for c in sc.get_completions(doc, None)]
print("[3] 输入 'git ' -> 前 5 项:", items[:5])
ok &= len(items) > 0

print()
print("VERIFY:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
