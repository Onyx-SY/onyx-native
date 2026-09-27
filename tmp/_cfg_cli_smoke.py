# -*- coding: utf-8 -*-
"""config-onyx-repl 新子命令冒烟测试（ptk 路径重定向到临时目录，不碰真实配置）。"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import bin.repl_config as rc  # noqa: E402

_T = tempfile.mkdtemp(prefix="onyx_ptk_")
rc._ptk_path = lambda: os.path.join(_T, "ptk.json")

print("=== list ===")
for line in rc.format_list():
    print(line)
print("=== repl list ===")
for line in rc.ptk_format_list():
    print(line)
print("=== colors list ===")
for line in rc.ptk_format_list("colors"):
    print(line)

print("=== set / get ===")
print("set ok:", rc.ptk_set("colors.completion-menu", "bg:#111111 #dddddd"))
print("get  ->", rc.ptk_get("colors.completion-menu"))
print("resolve ->", rc._ptk_resolve("completion-menu", "colors"),
      "|", rc._ptk_resolve("completion.show_hidden"), "|", rc._ptk_resolve("nope"))

print("=== bool / int / choice ===")
print("bool :", rc.ptk_set("auto_suggest.enabled", "no"), rc.ptk_get("auto_suggest.enabled"))
print("int  :", rc.ptk_set("completion.max_completions", "250"), rc.ptk_get("completion.max_completions"))
print("bad  :", rc.ptk_set("completion.max_completions", "abc"))
print("choice:", rc.ptk_set("auto_suggest.strategy", "frequency"), rc.ptk_get("auto_suggest.strategy"))

print("=== reset ===")
print("reset one:", rc.ptk_reset("colors.completion-menu"), rc.ptk_get("colors.completion-menu"))
print("reset all colors:", rc.ptk_reset_all("colors"))

print("=== json ===")
print(json.dumps(json.load(open(os.path.join(_T, "ptk.json"))), ensure_ascii=False)[:400])

print("=== 通用接口也认 ptk id ===")
print("get_value:", rc.get_value("colors.completion-menu"))
print("set_value:", rc.set_value("colors.scrollbar.button", "bg:#333333")[0],
      rc.get_value("colors.scrollbar.button"))
print("normalize str:", rc.normalize_value("colors.bottom-toolbar", "bg:#000000 #ffffff"))
print("choices bool:", rc.choices_of("auto_suggest.enabled"))
print("choices dyn :", rc.choices_of("auto_suggest.strategy"))
print("ALL PASS")
