# -*- coding: utf-8 -*-
"""更新 config-onyx-repl 的补全名单与参数补全表（新增 repl/colors 与 display.default-color）。"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CMD = os.path.join(ROOT, "etc", "cmd.json")
PARA = os.path.join(ROOT, "etc", "cmd", "cmd_para.json")

NEW_SUBS = [
    "display.default-color",
    "repl", "repl list", "repl get", "repl set", "repl reset",
    "colors", "colors list", "colors set", "colors reset",
]
NEW_OPTS = ["repl", "repl list", "repl get", "repl set", "repl reset",
            "colors", "colors list", "colors set", "colors reset"]
NEW_PARAMS = [
    "display.default-color",
    # ptk 颜色样式名（colors set <名> <样式>）
    "completion-menu", "completion-menu.completion", "completion-menu.completion.current",
    "completion-menu.meta", "completion-menu.meta.current",
    "scrollbar.background", "scrollbar.button", "bottom-toolbar",
    # ptk 其它项
    "completion.show_hidden", "completion.complete_while_typing",
    "completion.complete_in_thread", "completion.max_completions",
    "completion.reserve_space_for_menu",
    "history.memory_limit", "history.file_limit", "history.file_name",
    "auto_suggest.enabled", "auto_suggest.strategy",
]

with open(CMD, encoding="utf-8") as f:
    cmd = json.load(f)
subs = cmd["config-onyx-repl"]["subcommands"]
for s in NEW_SUBS:
    if s not in subs:
        subs.append(s)
with open(CMD, "w", encoding="utf-8") as f:
    json.dump(cmd, f, ensure_ascii=False, indent=2)
print("cmd.json subcommands:", len(subs))

with open(PARA, encoding="utf-8") as f:
    para = json.load(f)
lst = para if isinstance(para, list) else (para.get("cmd_para") or para.get("commands") or para.get("cmds") or [])
hit = None
for item in lst:
    if isinstance(item, dict) and item.get("cmd") == ["config-onyx-repl"]:
        hit = item
        break
if hit is None:
    raise SystemExit("cmd_para.json 里没找到 config-onyx-repl")
for o in NEW_OPTS:
    if o not in hit["opts"]:
        hit["opts"].append(o)
for p in NEW_PARAMS:
    if p not in hit["params"]:
        hit["params"].append(p)
with open(PARA, "w", encoding="utf-8") as f:
    json.dump(para, f, ensure_ascii=False, indent=2)
print("cmd_para opts:", len(hit["opts"]), "params:", len(hit["params"]))
print("OK")
