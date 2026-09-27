# -*- coding: utf-8 -*-
"""给 config-onyx-repl 的帮助文档补「主 REPL（ptk.json）」一节（中英）。"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "bin", "help", "help_info", "commands", "config-onyx-repl.json")

CN_ADD = """
主 REPL 配置（颜色 / 补全 / 历史 / 建议）——直接读写 ~/.config/onyx/ptk.json：
  config-onyx-repl repl list                 列出主 REPL 全部可配置项
  config-onyx-repl repl get  colors.completion-menu
  config-onyx-repl repl set  colors.completion-menu "bg:#111111 #dddddd"
  config-onyx-repl repl reset colors.completion-menu
  config-onyx-repl repl reset --all
  config-onyx-repl colors                    只看配色（补全菜单 / 滚动条 / 工具栏）
  config-onyx-repl colors set completion-menu "bg:#111111 #dddddd"

  · 颜色项是 prompt_toolkit 样式串：bg:#RRGGBB 背景、#RRGGBB 前景、bold 加粗；
  · 通用项新增 display.default-color（欢迎界面颜色 1=青 2=绿 3=黄 4=红）；
  · TUI 里可直接进「🎨 颜色 / 🧩 补全行为 / 📜 历史记录 / 💡 虚影建议」四个分区改；
  · 改完需重开 Onyx 或新起一次输入才生效。

"""

EN_ADD = """
Main-REPL config (colors / completion / history / suggest) - reads & writes ~/.config/onyx/ptk.json:
  config-onyx-repl repl list                 list every main-REPL setting
  config-onyx-repl repl get  colors.completion-menu
  config-onyx-repl repl set  colors.completion-menu "bg:#111111 #dddddd"
  config-onyx-repl repl reset colors.completion-menu
  config-onyx-repl repl reset --all
  config-onyx-repl colors                    colors only (menu / scrollbar / toolbar)
  config-onyx-repl colors set completion-menu "bg:#111111 #dddddd"

  · A color value is a prompt_toolkit style string: bg:#RRGGBB, #RRGGBB, bold;
  · New common setting: display.default-color (welcome banner color 1=cyan 2=green 3=yellow 4=red);
  · In the TUI you can enter the four sections: colors / completion / history / auto-suggest;
  · Takes effect after restarting Onyx or starting a new prompt.

"""

with open(P, encoding="utf-8") as f:
    d = json.load(f)
e = d["命令"]["config-onyx-repl"]

for key, add, marker in (("Chinese", CN_ADD, "适配系统："),
                         ("English", EN_ADD, "OS support:")):
    txt = e[key]
    if "colors set completion-menu" in txt:
        print(key, "已包含，跳过")
        continue
    i = txt.find(marker)
    e[key] = (txt[:i] + add + txt[i:]) if i >= 0 else (txt + "\n" + add)

with open(P, "w", encoding="utf-8") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)
print("help updated:", {k: len(v) for k, v in e.items()})
