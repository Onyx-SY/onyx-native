# -*- coding: utf-8 -*-
"""step-4/7 验证：共用补全候选函数 + REPL 补全器（三类候选）。

用法：python3 tmp/repl_completion_check.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bin.ai_interactive import (  # noqa: E402
    _AICompleter, _SLASH_COMMANDS_CN, _SLASH_COMMANDS_EN, completion_candidates,
)

FAILS = []


def check(name, cond, extra=""):
    print(f"{'✅' if cond else '❌'} {name}{(' | ' + str(extra)) if extra else ''}")
    if not cond:
        FAILS.append(name)
    return cond


def apply(text, cand):
    """按 (插入文本, 描述, 替换长度) 计算补全后的整行。"""
    value, _meta, replace_len = cand
    return text[: len(text) - replace_len] + value


def main():
    # ── 1. 斜杠命令头 ──
    cands = completion_candidates("/he", "chinese")
    check("斜杠头：/he → /help", [c[0] for c in cands] == ["/help"], cands)
    check("斜杠头：带描述", cands and bool(cands[0][1]), cands[0][1][:30] if cands else "")
    check("斜杠头：补全后整行正确", apply("/he", cands[0]) == "/help", apply("/he", cands[0]))
    allc = completion_candidates("/", "chinese")
    check("斜杠头：/ → 全部 29 条", len(allc) == len(_SLASH_COMMANDS_CN), len(allc))
    en = completion_candidates("/", "english")
    check("斜杠头：英文表可用且条数一致", len(en) == len(_SLASH_COMMANDS_EN), len(en))
    check("斜杠头：大小写不敏感", [c[0] for c in completion_candidates("/HE", "chinese")] == ["/help"])

    # ── 2. 参数枚举 ──
    c = completion_candidates("/lang ", "chinese")
    check("枚举：/lang → cn/en", [x[0] for x in c] == ["cn", "en"], [x[0] for x in c])
    check("枚举：补全后整行", apply("/lang ", c[0]) == "/lang cn", apply("/lang ", c[0]))
    c = completion_candidates("/param thinking ", "chinese")
    check("枚举：/param thinking → on/off", [x[0] for x in c] == ["on", "off"], [x[0] for x in c])
    c = completion_candidates("/mode n", "chinese")
    check("枚举：/mode n → normal", [x[0] for x in c] == ["normal"], [x[0] for x in c])
    c = completion_candidates("/help /co", "chinese")
    check("枚举：/help /co → /cost 之类", all(x[0].startswith("/co") for x in c) and len(c) >= 1,
          [x[0] for x in c])

    # ── 3. 路径 ──
    c = completion_candidates("/cd bin", "chinese")
    check("路径：/cd bin → bin 下的条目", bool(c) and all(x[0] for x in c), [x[0] for x in c][:5])
    check("路径：替换长度=basename 长度", bool(c) and c[0][2] == len("bin"), c[0] if c else None)
    c2 = completion_candidates("/cd bin/", "chinese")
    check("路径：/cd bin/ 列出目录内容", bool(c2), [x[0] for x in c2][:5])
    check("路径：目录带 /", any(x[0].endswith("/") for x in c2), [x[0] for x in c2][:5])
    c3 = completion_candidates("看看 ./bin", "chinese")
    check("路径：普通输入像路径才补（带前缀词）", isinstance(c3, list))

    # ── 4. 非补全场景 ──
    check("普通中文输入：无候选", completion_candidates("你好", "chinese") == [])
    check("已打完命令+空格的非路径参数：无候选", completion_candidates("/plus ", "chinese") == [])

    # ── 5. REPL 补全器（prompt_toolkit）仍工作 ──
    from prompt_toolkit.document import Document
    comp = _AICompleter(_SLASH_COMMANDS_CN, "chinese")
    got = [c.text for c in comp.get_completions(Document("/he", 3), None)]
    check("REPL 补全器：/he → /help", got == ["/help"], got)
    got = [c.text for c in comp.get_completions(Document("/lang ", 6), None)]
    check("REPL 补全器：/lang → cn/en", got == ["cn", "en"], got)
    got = [c.text for c in comp.get_completions(Document("/cd bin", 7), None)]
    check("REPL 补全器：/cd bin → 有候选", bool(got), got[:5])
    got = [c.text for c in comp.get_completions(Document("你好", 2), None)]
    check("REPL 补全器：普通输入无候选", got == [], got)

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 项失败：{FAILS}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
