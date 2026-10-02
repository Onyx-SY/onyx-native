# -*- coding: utf-8 -*-
"""修 has_pipeline 把 `||`（逻辑或）误判为管道。"""
import shutil

P = 'lib/parse.py'
s = open(P, encoding='utf-8').read()

OLD = "        elif char == '|' and not in_single and not in_double:\n            return True\n"
NEW = ("        elif char == '|' and not in_single and not in_double:\n"
       "            # `||` 是逻辑或，不是管道；`|&` 是管道+stderr，仍算管道。\n"
       "            if i + 1 < len(text) and text[i + 1] == '|':\n"
       "                continue\n"
       "            if i > 0 and text[i - 1] == '|':\n"
       "                continue\n"
       "            return True\n")

n = s.count(OLD)
assert n == 1, ('occurrences', n)
shutil.copy(P, P + '.bak3')
open(P, 'w', encoding='utf-8').write(s.replace(OLD, NEW, 1))
print('PIPE MISJUDGE FIX APPLIED')
