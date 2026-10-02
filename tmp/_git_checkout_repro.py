#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复现：git checkout <tab> 的补全结果。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from prompt_toolkit.document import Document
from lib.terminal.com import SmartCompleter

cmd_json = os.path.join(ROOT, "etc", "cmd.json")
com_json = os.path.join(ROOT, "etc", "cmdal.json")

cmds = ["git", "ls", "echo", "docker"]
sc = SmartCompleter(
    cmds,
    show_hidden=True,
    cmd_config_path=cmd_json,
    com_cmd_config_path=com_json,
    virtual_root=os.getcwd(),
    user_home_dir=os.path.expanduser("~"),
    history_buffer=[],
)

print("dynamic has git :", sc.dynamic_manager.has("git"))
print("dynamic commands:", sc.dynamic_manager.registry.commands()[:20])
print("scripts loaded  :", sc.dynamic_manager.registry._scripts_loaded)

for text in ["git ", "git checkout ", "git checkout ma", "git checkout -"]:
    doc = Document(text, len(text))
    ctx = sc._get_context(doc)
    print("\n>>> 输入:", repr(text))
    print("    ctx_type=%s current=%r cmd=%r node=%s" % (ctx[0], ctx[1], ctx[3], ctx[4]))
    out = []
    for c in sc.get_completions(doc, None):
        out.append((c.text, str(c.display_meta)))
    print("    补全(%d): %s" % (len(out), out[:15]))
