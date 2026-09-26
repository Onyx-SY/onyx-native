# -*- coding: utf-8 -*-
"""step-1 验证：pygments 恢复 / 缩进不再抛异常 / theme 可用。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
fails = []

# ── ① pygments 恢复 ─────────────────────────────────────────────
from lib.terminal import mul_line as ML
ML._ensure_pygments_loaded()
print("HAS_PYGMENTS =", ML.HAS_PYGMENTS, "| 缺失 lexer =", ML._PYGMENTS_MISSING)
if not ML.HAS_PYGMENTS:
    fails.append("HAS_PYGMENTS 仍为 False")
if ML._PYGMENTS_MISSING:
    fails.append(f"仍有缺失 lexer: {ML._PYGMENTS_MISSING}")
for name in ("BashLexer", "Python3Lexer", "JavaScriptLexer", "JsonLexer"):
    if getattr(ML, name, None) is None:
        fails.append(f"{name} 未注入")
print("关键 lexer 注入:", [n for n in ("BashLexer", "Python3Lexer", "JavaScriptLexer", "JsonLexer")
                          if getattr(ML, n, None) is not None])

# ── ② 多行输入拿到真 lexer + 缩进不抛异常 ────────────────────────
from prompt_toolkit.document import Document
from lib.terminal.mul_line import MultiLineInput

cases = [
    ("bash", "if true; then", 4),
    ("bash", "for i in 1 2 3; do", 4),
    ("bash", "echo hi", 0),
    ("python", "def f():", 4),
    ("python", "    return 1", 4),
    ("python", "else:", 0),
]
for syntax, line, expect in cases:
    ml = MultiLineInput(syntax=syntax, virtual_root="")
    has_lexer = ml.lexer is not None
    try:
        got = ml._compute_smart_indent(line, Document(line))
    except Exception as e:
        fails.append(f"{syntax}/{line!r} 抛异常 {e!r}")
        print(f"❌ {syntax:7} {line!r:22} 抛异常 {e!r}")
        continue
    ok = (got == expect)
    print(f"{'✅' if ok else '⚠️ '} {syntax:7} lexer={has_lexer!s:5} indent({line!r}) = {got} (期望 {expect})")
    if not has_lexer:
        fails.append(f"{syntax} 没拿到 lexer")

# ── ③ pygments 真的能产出彩色 token（用 raw_lexer：ptk 包装器没有 get_tokens）──
ml = MultiLineInput(syntax="bash", virtual_root="")
print("lexer 类型      =", type(ml.lexer).__name__, "| raw_lexer =", type(ml.raw_lexer).__name__)
if ml.raw_lexer is None:
    fails.append("raw_lexer 为 None，token 分析路径仍是死的")
else:
    toks = list(ml.raw_lexer.get_tokens("git commit -m 'x' | grep y"))
    kinds = {str(t) for t, _ in toks if t}
    print("bash lexer 产出的 token 类型数:", len(kinds), sorted(kinds)[:8])
    if len(kinds) < 3:
        fails.append("pygments token 类型过少，高亮可能没生效")

# ── ④ theme 可用 ────────────────────────────────────────────────
from lib.terminal.repl import theme
st = theme.build_style()
print("theme 样式条数:", len(theme.all_styles()))
for probe in ("tok.command", "tok.error", "completion-menu.completion.current", "toolbar", "help.key"):
    if not theme.style_for(probe):
        fails.append(f"theme 缺少 {probe}")
print("语义名解析:", {p: theme.style_for(p) for p in ("tok.command", "tok.error", "toolbar")})
print("旧名兼容:", theme.style_for("ansigreen bold"), "->", theme.LEGACY_ALIASES.get("ansigreen bold"))

print("\nFAILS:", fails if fails else "none")
sys.exit(1 if fails else 0)
