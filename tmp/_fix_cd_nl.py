# -*- coding: utf-8 -*-
"""修「无输出命令（如 cd）会多一个换行」。

根因：cd 无输出时，哨兵之前只剩 shell 的控制序列 `\\x1b[?2004l\\r`，
_head 以 '\\r' 结尾 → endswith('\\n') 为假 → 补了一个 '\\n' → 凭空多一空行。
（输出以 \\r\\n 结尾的命令如 ls 不触发，所以「有时候有、有时候没有」。）
"""
import shutil

P = 'lib/terminal/exe.py'
shutil.copy(P, P + '.bak3')
s = open(P, encoding='utf-8').read()

A = """                    if _head:
                        if not _head.endswith(b'\\n'):
                            _head += b'\\n'   # 输出未以换行结束 → 补一个"""
B = """                    if _head:
                        # 末尾是 \\r 时不补：那是 shell 控制序列的尾巴
                        # （如 \\x1b[?2004l\\r），补了会凭空多一个空行 ——
                        # cd 这类「无输出」命令最常见。
                        if not _head.endswith((b'\\n', b'\\r')):
                            _head += b'\\n'   # 输出未以换行结束 → 补一个"""
assert s.count(A) == 1, s.count(A)
s = s.replace(A, B, 1)

open(P, 'w', encoding='utf-8').write(s)
print('CD-NEWLINE FIX APPLIED')
