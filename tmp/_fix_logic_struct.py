# -*- coding: utf-8 -*-
"""修 is_shell_logic_structure：多行函数定义 `f() {` … `}` 不被识别。

`f() { echo a; }` 单行 → False 正确；
但 `f() {\n echo a\n}`（多行）→ 也应 True，否则 REPL 不进入多行模式 → 逐行提交报错。
"""
import shutil

P = 'lib/parse.py'
s = open(P, encoding='utf-8').read()

OLD = """        # 检测 () 子shell 和 {} 块（多行）
        if stripped.startswith('(') or stripped.startswith('{'):
            return True
        if '\\n{' in stripped or '\\n(' in stripped:
            return True
"""
NEW = """        # 检测 () 子shell 和 {} 块（多行）
        if stripped.startswith('(') or stripped.startswith('{'):
            return True
        if '\\n{' in stripped or '\\n(' in stripped:
            return True
        # 多行函数定义 / 花括号块：`f() {` … `}`（`{` 在行尾且有配对 `}`）
        # 注意排除 `echo {1..3}` 这类展开（`{` 不在行尾）。
        if re.search(r'\\{\\s*$', cmd_str, re.M) and '}' in cmd_str:
            return True
"""

n = s.count(OLD)
assert n == 1, ('occurrences', n)
shutil.copy(P, P + '.bak5')
open(P, 'w', encoding='utf-8').write(s.replace(OLD, NEW, 1))
print('LOGIC STRUCT FIX APPLIED')
