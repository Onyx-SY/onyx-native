# -*- coding: utf-8 -*-
"""step-3：把 config-onyx-repl 加进补全名单（etc/cmd.json + etc/cmd/cmd_para.json）、
权限白名单（etc/cmdal.json）与 help 教程（bin/help/help_info/commands/）。"""
import io
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SETTING_IDS = ["language", "debug-times", "debug-parsecmd", "clean-log-time",
               "adv_danger_cmd_prompt", "mcp", "spring-mode", "builtin-adv-syntax",
               "sandbox", "prompt-style", "history-len"]
SUBCOMMANDS = ["ui", "list", "get", "set", "reset", "--all"] + SETTING_IDS + ["true", "false"]

# ── 1) 补全名单（真正被补全读取的那份）：etc/cmd.json ──
P = os.path.join(ROOT, "etc", "cmd.json")
data = json.load(io.open(P, encoding="utf-8"))
if "config-onyx-repl" in data:
    print("SKIP etc/cmd.json：已存在")
else:
    new = {}
    for k, v in data.items():
        new[k] = v
        if k == "manage":
            new["config-onyx-repl"] = {"subcommands": SUBCOMMANDS,
                                       "options": ["-h", "--help"]}
    if "config-onyx-repl" not in new:          # manage 不在时兜底追加
        new["config-onyx-repl"] = {"subcommands": SUBCOMMANDS, "options": ["-h", "--help"]}
    tmp = P + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        json.dump(new, f, ensure_ascii=False, indent=2)
    os.replace(tmp, P)
    print("OK   etc/cmd.json：已插入 config-onyx-repl")

# ── 2) 权限白名单：etc/cmdal.json（否则 low/mid 模式下命令会被拦）──
C = os.path.join(ROOT, "etc", "cmdal.json")
cal = json.load(io.open(C, encoding="utf-8"))
changed = False
for mode, cfg in (cal.get("perm_limit") or {}).items():
    lst = cfg.get("allow_commands")
    if isinstance(lst, list) and "config-onyx-repl" not in lst:
        # 插到 manage 之后，保持同类相邻
        if "manage" in lst:
            lst.insert(lst.index("manage") + 1, "config-onyx-repl")
        else:
            lst.append("config-onyx-repl")
        changed = True
        print(f"OK   etc/cmdal.json[{mode}]：已加入白名单")
if not changed:
    print("SKIP etc/cmdal.json：已存在")
else:
    tmp = C + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        json.dump(cal, f, ensure_ascii=False, indent=1)
    os.replace(tmp, C)

# ── 3) 参数补全表：etc/cmd/cmd_para.json（文本插入，保持原格式/宽松 JSON）──
Q = os.path.join(ROOT, "etc", "cmd", "cmd_para.json")
q = io.open(Q, encoding="utf-8").read()
if "config-onyx-repl" in q:
    print("SKIP cmd_para.json：已存在")
else:
    anchor = '''    {
      "cmd": ["manage"],
      "opts": [],
      "params": ["set", "clean"],
      "type": "string"
    },
'''
    block = '''    {
      "cmd": ["config-onyx-repl"],
      "opts": ["ui", "list", "get", "set", "reset", "--all", "-h", "--help"],
      "params": ["%s"],
      "type": "string"
    },
''' % '", "'.join(SETTING_IDS + ["true", "false"])
    if q.count(anchor) != 1:
        print(f"FAIL cmd_para.json：锚点命中 {q.count(anchor)} 次")
    else:
        q = q.replace(anchor, anchor + block, 1)
        tmp = Q + ".tmp"
        io.open(tmp, "w", encoding="utf-8").write(q)
        os.replace(tmp, Q)
        print("OK   cmd_para.json：已插入")

# ── 4) help 教程：bin/help/help_info/commands/config-onyx-repl.json ──
H = os.path.join(ROOT, "bin", "help", "help_info", "commands", "config-onyx-repl.json")
zh = """用法：config-onyx-repl [子命令]

功能：主 REPL 统一配置入口。无参数直接打开 TUI 配置界面快速配置；
      也支持参数式读写（与 manage set 共用同一套 JSON 配置，两边永远一致）。

子命令：
  （无参数）/ ui        打开 TUI 配置界面
                        ↑↓ 选择 · Enter 修改 · r 恢复该项默认 · R 恢复全部默认 · q 退出
  list                 列出全部配置项与当前值（* = 已改动）
  get  <项>            读取某项当前值
  set  <项> <值>       设置某项（值非法直接报错，不会写坏配置）
  reset <项>           恢复某项默认值
  reset --all          恢复全部默认值

可配置项：
  language              界面语言（Chinese/English，短码 zh/cn/en）
  debug-times           命令耗时输出（true/false）
  debug-parsecmd        命令解析调试（true/false）
  clean-log-time        日志保留天数（正整数或 false）
  adv_danger_cmd_prompt adv 危险命令二次确认（true/false）
  mcp                   MCP 协议工具（true/false）
  spring-mode           启动问候语（true/false）
  builtin-adv-syntax    内置命令高级语法（true/false）
  sandbox               AI 沙箱（true/false）
  prompt-style          提示符样式（config.json 里的样式名，如 onyx/kali/termux）
  history-len           历史记录条数上限（正整数）

教程示例：
  1) 打开图形化配置界面（推荐）：
       config-onyx-repl
  2) 看看现在都有哪些配置、当前值是什么：
       config-onyx-repl list
  3) 关掉命令耗时输出、把日志保留改成 7 天：
       config-onyx-repl set debug-times false
       config-onyx-repl set clean-log-time 7
  4) 把界面语言切成英文（短码也行）：
       config-onyx-repl set language en
  5) 改完后悔了，恢复某一项 / 全部：
       config-onyx-repl reset language
       config-onyx-repl reset --all

提示：
  · 标「重启后生效」的项（language / mcp / sandbox / prompt-style / history-len）
    需要重启 Onyx 才会完全生效；
  · 与 manage set 等价，用哪个都行，改的是同一份配置；
  · 补全已支持：输入 config-onyx-repl set 后按 Tab 可补全配置项名。

适配系统：Windows/Linux/Termux 全兼容"""

en = """Usage: config-onyx-repl [subcommand]

What it does: the single config entry for the main REPL. With no argument it opens a
      TUI config screen for quick configuration; it also supports a command-line style
      that shares the exact same JSON config as manage set.

Subcommands:
  (no args) / ui        open the TUI config screen
                        up/down move · Enter edit · r reset item · R reset all · q quit
  list                  list every setting and its current value (* = changed)
  get  <item>           read one setting
  set  <item> <value>   set one setting (invalid values are rejected, config stays intact)
  reset <item>          reset one setting to default
  reset --all           reset everything to default

Settings:
  language              UI language (Chinese/English; zh/cn/en accepted)
  debug-times           show command timing (true/false)
  debug-parsecmd        command parse debug (true/false)
  clean-log-time        log retention in days (positive integer or false)
  adv_danger_cmd_prompt confirm dangerous commands in adv mode (true/false)
  mcp                   MCP tools (true/false)
  spring-mode           startup greeting (true/false)
  builtin-adv-syntax    advanced syntax for builtin commands (true/false)
  sandbox               AI sandbox (true/false)
  prompt-style          prompt style name from config.json (onyx/kali/termux/...)
  history-len           history length limit (positive integer)

Tutorial:
  1) Open the graphical config screen (recommended):
       config-onyx-repl
  2) See every setting and its current value:
       config-onyx-repl list
  3) Turn off command timing and keep logs for 7 days:
       config-onyx-repl set debug-times false
       config-onyx-repl set clean-log-time 7
  4) Switch the UI to English (short codes work too):
       config-onyx-repl set language en
  5) Changed your mind - reset one item / everything:
       config-onyx-repl reset language
       config-onyx-repl reset --all

Notes:
  · Items marked "takes effect after restart" (language / mcp / sandbox / prompt-style /
    history-len) need an Onyx restart to fully apply;
  · Equivalent to manage set - same config file, use whichever you like;
  · Completion is wired up: type config-onyx-repl set and press Tab for setting names.

OS support: Windows / Linux / Termux"""

if os.path.exists(H):
    print("SKIP help json：已存在")
else:
    os.makedirs(os.path.dirname(H), exist_ok=True)
    with io.open(H, "w", encoding="utf-8") as f:
        json.dump({"命令": {"config-onyx-repl": {"Chinese": zh, "English": en}}},
                  f, ensure_ascii=False, indent=2)
    print("OK   help json：已创建")
