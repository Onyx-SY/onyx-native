# -*- coding: utf-8 -*-
"""step-6：TUI 文案键（Ctrl+C=取消 / Tab=补全 / 退出键说明），中英对齐。

用法：python3 tmp/add_i18n_keys3.py
"""
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LANG_JSON = os.path.join(ROOT, "bin", "ai_lib", "lang.json")
BACKUP = os.path.join(ROOT, "tmp", "lang.json.bak3")

NEW_KEYS = {
    "tui_single_hint": (
        "Enter=发送 · Alt+Enter=多行 · Tab=补全 · Ctrl+C=取消 · Ctrl+Q=退出",
        "Enter=send · Alt+Enter=multiline · Tab=complete · Ctrl+C=cancel · Ctrl+Q=quit",
    ),
    "tui_ml_hint": (
        "📝 多行模式 · Enter=换行 · Alt+Enter=发送 · Ctrl+C=取消 · Ctrl+Q=退出",
        "📝 Multiline · Enter=newline · Alt+Enter=send · Ctrl+C=cancel · Ctrl+Q=quit",
    ),
    "tui_welcome": (
        "🤖 Onyx AI — TUI 模式。底部输入框可直接对 AI 说话；AI 运行时输入会排队，当前轮结束后依次处理。\n"
        "Enter=发送 · Alt+Enter=多行 · Tab=补全 · →=接受虚影 · Ctrl+C=取消当前生成 · Ctrl+Q / Ctrl+D=退出。",
        "🤖 Onyx AI — TUI mode. Type in the box below to talk to the AI; input while the AI runs is queued and "
        "handled after the current round.\n"
        "Enter=send · Alt+Enter=multiline · Tab=complete · →=accept ghost · Ctrl+C=cancel generation · "
        "Ctrl+Q / Ctrl+D=quit.",
    ),
    "tui_cancelling": (
        "⏹ 已请求取消当前生成…",
        "⏹ Cancelling the current generation…",
    ),
    "tui_ctrlc_hint": (
        "（输入框已空）按 Ctrl+Q 或 Ctrl+D 退出 TUI",
        "(input already empty) press Ctrl+Q or Ctrl+D to quit the TUI",
    ),
}


def main() -> int:
    with open(LANG_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
    shutil.copy2(LANG_JSON, BACKUP)
    added = 0
    for key, (cn, en) in NEW_KEYS.items():
        for lang, text in (("chinese", cn), ("english", en)):
            if key not in data[lang]:
                added += 1
            data[lang][key] = text
    with open(LANG_JSON + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(LANG_JSON + ".tmp", LANG_JSON)
    cn, en = set(data["chinese"]), set(data["english"])
    if cn != en:
        print(f"❌ 中英键不一致：{sorted(cn ^ en)}")
        return 1
    print(f"✅ 写入 {len(NEW_KEYS)} 个键（新增 {added}），中英各 {len(cn)} 条，完全对齐")
    return 0


if __name__ == "__main__":
    sys.exit(main())
