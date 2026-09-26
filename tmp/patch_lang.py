# -*- coding: utf-8 -*-
"""lang.json：新增 tui_banner_tag + 精简 tui_welcome（保持原格式，仅替换两处文本）。"""
import io
import json

P = "bin/ai_lib/lang.json"
src = io.open(P, encoding="utf-8").read()

OLD_CN = ('    "tui_title": "Onyx AI — TUI",\n'
          '    "tui_welcome": "🤖 欢迎使用 Onyx AI（TUI）。直接在下方输入框提问即可；'
          'AI 运行时输入会排队，并在下一轮开始时作为引导注入。",\n')
NEW_CN = ('    "tui_title": "Onyx AI — TUI",\n'
          '    "tui_banner_tag": "AI 终端 · 终端里的软件工程助手",\n'
          '    "tui_welcome": "直接输入问题，Enter 发送 · Alt+Enter 多行 · AI 运行时输入排队引导",\n')

OLD_EN = ('    "tui_title": "Onyx AI — TUI",\n'
          '    "tui_welcome": "🤖 Welcome to Onyx AI (TUI). Just type below; input sent while the '
          'AI is running is queued and injected as guidance at the start of the next round.",\n')
NEW_EN = ('    "tui_title": "Onyx AI — TUI",\n'
          '    "tui_banner_tag": "AI terminal · your software-engineering copilot",\n'
          '    "tui_welcome": "Type a question, Enter to send · Alt+Enter for multiline · '
          'input queues while the AI runs",\n')

for old, new, tag in ((OLD_CN, NEW_CN, "chinese"), (OLD_EN, NEW_EN, "english")):
    n = src.count(old)
    assert n == 1, f"{tag}: {n}"
    src = src.replace(old, new)
    print("✅", tag)

io.open(P, "w", encoding="utf-8").write(src)

d = json.load(io.open(P, encoding="utf-8"))       # 校验 JSON 合法
print("json ok:", d["chinese"]["tui_banner_tag"], "|", d["english"]["tui_welcome"])
