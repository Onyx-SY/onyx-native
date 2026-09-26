# -*- coding: utf-8 -*-
"""step-2 + step-3：默认模式改 TUI；etc/cmd.json 的 ai 补全项加 repl/tui。

用法：python3 tmp/patch_default_tui.py
"""
import io
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODE = os.path.join(ROOT, "bin", "ai_lib", "mode.py")
CMDJSON = os.path.join(ROOT, "etc", "cmd.json")

OLD_MODE = '\nDEFAULT_AI_MODE = "repl"\n'
NEW_MODE = '\nDEFAULT_AI_MODE = "tui"\n'


def main() -> int:
    # ── step-2：默认模式 ──
    with io.open(MODE, "r", encoding="utf-8") as f:
        src = f.read()
    if src.count(OLD_MODE) != 1:
        print(f"❌ mode.py：匹配 {src.count(OLD_MODE)} 次")
        return 1
    src = src.replace(OLD_MODE, NEW_MODE)
    with io.open(MODE + ".tmp", "w", encoding="utf-8") as f:
        f.write(src)
    os.replace(MODE + ".tmp", MODE)
    print("✅ mode.py：DEFAULT_AI_MODE = \"tui\"（ai repl / -repl 仍可覆盖）")

    # ── step-3：etc/cmd.json 的 ai 补全项 ──
    shutil.copy2(CMDJSON, os.path.join(ROOT, "tmp", "cmd.json.bak"))
    with io.open(CMDJSON, "r", encoding="utf-8") as f:
        data = json.load(f)
    ai = data.get("ai")
    if not isinstance(ai, dict):
        print("❌ etc/cmd.json：找不到 ai 段")
        return 1
    subs = ai.setdefault("subcommands", [])
    added = []
    for name in ("repl", "tui"):
        if name not in subs:
            subs.insert(0, name)
            added.append(name)
    opts = ai.setdefault("options", [])
    for opt in ("-repl", "--repl", "-tui", "--tui"):
        if opt not in opts:
            opts.append(opt)
            added.append(opt)
    with io.open(CMDJSON + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(CMDJSON + ".tmp", CMDJSON)
    print(f"✅ etc/cmd.json：ai 新增补全项 {added}")
    print(f"   subcommands={data['ai']['subcommands']}")
    print(f"   options={data['ai']['options']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
