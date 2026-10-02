#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端：把进程 cwd 切到真实 git 仓库，模拟 REPL 里 git checkout <tab>。"""
import os
import sys

ROOT = "/storage/emulated/0/abPython/PythonProject/工具/Hacker--V1.00.1/src/Hacker/onyx-test/home/u0_a305/onyx/onyx"
REPO = "/storage/emulated/0/abPython/PythonProject/工具/Hacker--V1.00.1/src/Hacker/onyx-test/home/u0_a305/onyx-server/onyx-server-fastapi_bak_user"
sys.path.insert(0, ROOT)

from prompt_toolkit.document import Document
from lib.terminal.com import SmartCompleter
from lib.terminal.dynamic_cmd import run_command, default_script_dirs

os.chdir(REPO)
print("cwd =", os.getcwd())
print("git branch ->", run_command(["git", "branch", "--format=%(refname:short)"]))
print("dirs ->", [d for d in default_script_dirs() if os.path.isdir(d)])

sc = SmartCompleter(
    ["git"],
    cmd_config_path=os.path.join(ROOT, "etc", "cmd.json"),
    com_cmd_config_path=os.path.join(ROOT, "etc", "cmdal.json"),
    virtual_root=REPO,
    user_home_dir=os.path.expanduser("~"),
    history_buffer=[],
)
print("has git:", sc.dynamic_manager.has("git"))

for text in ["git checkout ", "git checkout ma", "git branch "]:
    doc = Document(text, len(text))
    ctx = sc._get_context(doc)
    items = [(c.text, str(c.display_meta)) for c in sc.get_completions(doc, None)]
    print("输入 %-16r ctx=%-8s -> %s" % (text, ctx[0], items[:10]))
