# -*- coding: utf-8 -*-
"""step-1：向 bin/ai_lib/lang.json 追加 TUI / REPL 界面文案键（中英一一对应）。

用法：python3 tmp/add_i18n_keys.py
特性：幂等（已存在则覆盖为最新文案）、原子写、保留 2 空格缩进 + 非 ASCII 原样。
"""
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LANG_JSON = os.path.join(ROOT, "bin", "ai_lib", "lang.json")
BACKUP = os.path.join(ROOT, "tmp", "lang.json.bak")

# key -> (chinese, english)
NEW_KEYS = {
    # ── TUI（Textual 全屏界面）──
    "tui_title": ("Onyx AI — TUI", "Onyx AI — TUI"),
    "tui_welcome": (
        "🤖 Onyx AI — TUI 模式。底部输入框可直接对 AI 说话；AI 运行时输入会排队，当前轮结束后依次处理。\n"
        "Enter=发送 · Alt+Enter=多行模式 · →=接受虚影补全 · Ctrl+Q / Ctrl+D=退出。",
        "🤖 Onyx AI — TUI mode. Type in the box below to talk to the AI; input while the AI runs is queued and "
        "handled after the current round.\n"
        "Enter=send · Alt+Enter=multiline · →=accept ghost completion · Ctrl+Q / Ctrl+D=quit.",
    ),
    "tui_placeholder": (
        "输入消息后回车…（AI 运行时输入将排队引导）",
        "Type a message, press Enter… (queued while the AI runs)",
    ),
    "tui_queued": (
        "⏳ 已排队（当前轮结束后处理）：{text}",
        "⏳ Queued (handled after the current round): {text}",
    ),
    "tui_todo_title": ("📋 TODO", "📋 TODO"),
    "tui_tasks_none": ("（暂无任务）", "(no tasks)"),
    "tui_files_title": ("📁 FILES", "📁 FILES"),
    "tui_yes": ("是 (y)", "Yes (y)"),
    "tui_no": ("否 (n)", "No (n)"),
    "tui_captcha_label": ("验证码", "Captcha"),
    "tui_captcha_hint": ("输入验证码后回车", "Type the captcha, then Enter"),
    "tui_start_fail": (
        "⚠️ TUI 启动失败（{err}），已回退 REPL。",
        "⚠️ TUI failed to start ({err}); falling back to REPL.",
    ),
    # 超长输入折叠：中间省略行（{n} = 被省略的行数）
    "tui_omitted": ("⋯ 此处省略 {n} 行 ⋯", "⋯ {n} lines omitted ⋯"),
    "tui_ml_hint": (
        "📝 多行模式 · Enter=换行 · Alt+Enter=发送",
        "📝 Multiline · Enter=newline · Alt+Enter=send",
    ),
    "tui_single_hint": (
        "Enter=发送 · Alt+Enter=多行模式",
        "Enter=send · Alt+Enter=multiline",
    ),
    # ── REPL（prompt_toolkit）底部工具栏 / 多行提示 ──
    "repl_toolbar_normal": (
        " Enter=发送 · Alt+Enter=多行模式 · →=接受虚影 · Esc/Ctrl+D=退出 · /help=帮助 ",
        " Enter=send · Alt+Enter=multiline · →=accept ghost · Esc/Ctrl+D=exit · /help=help ",
    ),
    "repl_toolbar_multiline": (
        " 📝 多行模式：Enter=换行暂存 · Alt+Enter=统一发送 · Ctrl+C=清空退出 ",
        " 📝 Multiline: Enter=newline · Alt+Enter=send all · Ctrl+C=clear & exit ",
    ),
    "repl_multiline_prefix": ("[多行] ", "[ML] "),
}


def main() -> int:
    with open(LANG_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)

    for lang in ("chinese", "english"):
        if lang not in data or not isinstance(data[lang], dict):
            print(f"❌ lang.json 缺少 {lang} 段")
            return 1

    # 备份
    shutil.copy2(LANG_JSON, BACKUP)

    added = 0
    for key, (cn, en) in NEW_KEYS.items():
        for lang, text in (("chinese", cn), ("english", en)):
            if key not in data[lang]:
                added += 1
            data[lang][key] = text

    tmp = LANG_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, LANG_JSON)

    # 校验：中英键集合完全一致
    cn_keys, en_keys = set(data["chinese"]), set(data["english"])
    if cn_keys != en_keys:
        print(f"❌ 中英键不一致：仅中文 {sorted(cn_keys - en_keys)} / 仅英文 {sorted(en_keys - cn_keys)}")
        return 1

    print(f"✅ 写入 {len(NEW_KEYS)} 个键（新增 {added} 个），中英键各 {len(cn_keys)} 条，完全对齐")
    for key in ("tui_omitted", "tui_welcome", "repl_toolbar_normal"):
        print(f"  {key} → {data['chinese'][key][:40]!r} | {data['english'][key][:40]!r}")
    print(f"备份：{BACKUP}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
