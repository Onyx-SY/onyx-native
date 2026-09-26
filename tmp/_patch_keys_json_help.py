# -*- coding: utf-8 -*-
"""step-4c：补全名单 + 参数补全表 + help 教程补 keys 子命令。"""
import io
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEYS_SUBS = ["keys", "keys set", "keys reset", "keys list", "--all"]

# ── 1) etc/cmd.json ──
P = os.path.join(ROOT, "etc", "cmd.json")
d = json.load(io.open(P, encoding="utf-8"))
subs = d.get("config-onyx-repl", {}).get("subcommands")
if subs is None:
    print("FAIL etc/cmd.json：缺 config-onyx-repl")
else:
    added = [k for k in KEYS_SUBS if k not in subs]
    if added:
        for k in ("keys", "keys set", "keys reset", "keys list", "--all"):
            if k not in subs:
                subs.append(k)
        tmp = P + ".tmp"
        json.dump(d, io.open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        os.replace(tmp, P)
        print(f"OK   etc/cmd.json：补 {added}")
    else:
        print("SKIP etc/cmd.json")

# ── 2) etc/cmd/cmd_para.json（文本插入，保持宽松 JSON 原格式）──
Q = os.path.join(ROOT, "etc", "cmd", "cmd_para.json")
q = io.open(Q, encoding="utf-8").read()
if '"keys set"' in q:
    print("SKIP cmd_para.json")
else:
    old = '''      "opts": ["ui", "list", "get", "set", "reset", "--all", "-h", "--help"],'''
    new = '''      "opts": ["ui", "list", "get", "set", "reset", "--all", "-h", "--help", "keys", "keys set", "keys reset", "keys list"],'''
    assert q.count(old) == 1, q.count(old)
    q = q.replace(old, new, 1)
    old2 = '''      "params": ["language",'''
    new2 = '''      "params": ["history_up", "history_down", "prefix_history_up", "prefix_history_down", "completion_next", "completion_prev", "completion_page_up", "completion_page_down", "completion_menu_up", "completion_menu_down", "completion_trigger", "completion_alt_next", "completion_alt_prev", "completion_lock", "clear_screen", "multiline_editor", "language",'''
    assert q.count(old2) == 1, q.count(old2)
    q = q.replace(old2, new2, 1)
    tmp = Q + ".tmp"
    io.open(tmp, "w", encoding="utf-8").write(q)
    os.replace(tmp, Q)
    print("OK   cmd_para.json：补 keys 与键位动作名")

# ── 3) help 教程 ──
H = os.path.join(ROOT, "bin", "help", "help_info", "commands", "config-onyx-repl.json")
h = json.load(io.open(H, encoding="utf-8"))
entry = h["命令"]["config-onyx-repl"]
CN_ADD = '''
  keys                 列出主 REPL 键位（TUI 里 Enter 进入，按一下键即可改）
  keys set  <动作> <键> 改主 REPL 键位（写回 ~/.config/onyx/ptk.json）
  keys reset <动作>|--all   恢复主 REPL 键位默认

主 REPL 可改的动作（16 个）：
  history_up / history_down              历史上一条 / 下一条
  prefix_history_up / prefix_history_down 前缀历史（默认 Alt+↑ / Alt+↓）
  completion_next / completion_prev      补全下一项 / 上一项
  completion_page_up / completion_page_down 补全菜单翻页
  completion_menu_up / completion_menu_down 补全菜单选择上下
  completion_trigger                     手动触发补全（默认 Ctrl+Space）
  completion_alt_next / completion_alt_prev 补全备用键（默认 Ctrl+N / Ctrl+P）
  completion_lock                        切换补全锁定（默认 Alt+Space）
  clear_screen                           清屏
  multiline_editor                       **进入全屏多行编辑区（默认 Alt+Enter）**

多行编辑区（Alt+Enter 进入）：
  Enter=换行 · Alt+Enter / Ctrl+D=提交 · Ctrl+C=取消；占满整屏，可滚动回看、能改任意行。
'''
EN_ADD = '''
  keys                 list main-REPL key bindings (Enter in the TUI; press a key to rebind)
  keys set  <action> <key>  rebind a main-REPL key (writes ~/.config/onyx/ptk.json)
  keys reset <action>|--all reset main-REPL key bindings

Rebindable main-REPL actions (16):
  history_up / history_down              history previous / next
  prefix_history_up / prefix_history_down prefix history (Alt+Up / Alt+Down)
  completion_next / completion_prev      completion next / previous
  completion_page_up / completion_page_down completion page up / down
  completion_menu_up / completion_menu_down completion menu up / down
  completion_trigger                     trigger completion (Ctrl+Space)
  completion_alt_next / completion_alt_prev completion alt keys (Ctrl+N / Ctrl+P)
  completion_lock                        toggle completion lock (Alt+Space)
  clear_screen                           clear screen
  multiline_editor                       **open the full-screen multi-line editor (Alt+Enter)**

Multi-line editor (entered with Alt+Enter):
  Enter=newline · Alt+Enter / Ctrl+D=submit · Ctrl+C=cancel; full screen, scrollable, editable.
'''
if "keys set" in entry["Chinese"]:
    print("SKIP help json")
else:
    entry["Chinese"] = entry["Chinese"].replace(
        "\n教程示例：", CN_ADD + "\n教程示例：", 1)
    entry["English"] = entry["English"].replace(
        "\nTutorial:", EN_ADD + "\nTutorial:", 1)
    tmp = H + ".tmp"
    json.dump(h, io.open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    os.replace(tmp, H)
    print("OK   help json：补 keys 教程与多行编辑区说明")
