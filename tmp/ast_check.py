# -*- coding: utf-8 -*-
"""语法检查：python3 tmp/ast_check.py <文件...>"""
import ast
import sys

bad = 0
for p in sys.argv[1:]:
    try:
        ast.parse(open(p, encoding="utf-8").read())
        print(f"✅ AST OK  {p}")
    except SyntaxError as e:
        print(f"❌ {p}:{e.lineno} {e.msg}")
        bad += 1
sys.exit(1 if bad else 0)
