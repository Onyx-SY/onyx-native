# -*- coding: utf-8 -*-
"""诊断：主 REPL 输入层现在的实际状态（单行高亮 / 多行高亮 / 缩进）。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

print("=" * 70)
print("A) pygments 加载状态（多行高亮的总开关）")
print("=" * 70)
from lib.terminal import mul_line as ML
ML._ensure_pygments_loaded()
print("HAS_PYGMENTS =", ML.HAS_PYGMENTS)
print("缺失 lexer    =", getattr(ML, "_PYGMENTS_MISSING", "(无此属性)"))

print()
print("=" * 70)
print("B) 单行高亮：CommandLexer（AST 分词，不走 pygments）")
print("=" * 70)
from lib.terminal.com import CommandLexer
from prompt_toolkit.document import Document
lx = CommandLexer(valid_commands={"ls", "git", "cd", "echo"}, virtual_root="")
for sample in ["git status -s /etc/passwd", 'echo "hello world" $HOME', "ls -la | grep py"]:
    toks = lx.lex_document(Document(sample))(0)
    print(f"  {sample!r}")
    for style, text in toks:
        if text:
            print(f"      {style!r:32} {text!r}")

print()
print("=" * 70)
print("C) 这些 style 名在会话里能不能解析成颜色")
print("=" * 70)
from prompt_toolkit.styles import Style, merge_styles, default_ui_style
from prompt_toolkit.styles.defaults import default_pygments_style
from lib.terminal.com import COLORS, META_COLORS
names = sorted({s for s, _ in lx.lex_document(Document("git status -s /etc/passwd"))(0) if s})
merged = merge_styles([default_pygments_style(), default_ui_style(), Style.from_dict({})])
for n in names:
    try:
        attrs = merged.get_attrs_for_style_str(n)
        print(f"  {n!r:28} -> color={attrs.color} bgcolor={attrs.bgcolor} bold={attrs.bold}")
    except Exception as e:
        print(f"  {n!r:28} -> 解析失败 {e!r}")

print()
print("=" * 70)
print("D) 多行缩进：_compute_smart_indent（审计称引用不存在的属性）")
print("=" * 70)
from lib.terminal.mul_line import MultiLineInput
ml = MultiLineInput(syntax="bash", virtual_root="")
print("  MultiLineInput 有 .lexer ? ", hasattr(ml, "lexer"))
print("  MultiLineInput 有 ._pygments_lexer ? ", hasattr(ml, "_pygments_lexer"))
try:
    print("  indent('if true; then') =", ml._compute_smart_indent("if true; then", Document("if true; then")))
except Exception as e:
    print("  ❌ 调用即抛异常:", repr(e))
