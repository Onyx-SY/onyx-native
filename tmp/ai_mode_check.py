# -*- coding: utf-8 -*-
"""step-2/7 验证：裸 ai 默认进 TUI；ai repl / -repl 覆盖；带问题的一次性调用不受影响。

用法：python3 tmp/ai_mode_check.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bin.ai_lib import mode  # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(f"{'✅' if cond else '❌'} {name}{(' | ' + str(extra)) if extra else ''}")
    if not cond:
        FAILS.append(name)
    return cond


def main():
    check("DEFAULT_AI_MODE = tui", mode.DEFAULT_AI_MODE == "tui", mode.DEFAULT_AI_MODE)
    check("裸 ai → tui", mode.resolve_ai_mode() == "tui", mode.resolve_ai_mode())
    check("ai repl → repl", mode.resolve_ai_mode("repl") == "repl")
    check("ai -repl → repl", mode.resolve_ai_mode("repl") == "repl")
    check("ai -tui → tui", mode.resolve_ai_mode("tui") == "tui")

    os.environ["ONYX_AI_MODE"] = "repl"
    try:
        check("ONYX_AI_MODE=repl 覆盖默认", mode.resolve_ai_mode() == "repl", mode.resolve_ai_mode())
    finally:
        os.environ.pop("ONYX_AI_MODE", None)

    for parts, want_flag, want_len in (
        (["ai"], None, 1),
        (["ai", "repl"], "repl", 1),
        (["ai", "-repl"], "repl", 1),
        (["ai", "tui"], "tui", 1),
        (["ai", "-tui"], "tui", 1),
        (["ai", "帮我写个脚本"], None, 2),
    ):
        flag, eff = mode.split_mode_flag(list(parts))
        check(f"split_mode_flag({parts}) → flag={want_flag}", flag == want_flag and len(eff) == want_len,
              f"flag={flag} eff={eff}")

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 项失败：{FAILS}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
