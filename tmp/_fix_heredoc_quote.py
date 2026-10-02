# -*- coding: utf-8 -*-
"""修 source 脚本里带引号的 heredoc 定界符。

`python3 - "$1" <<'PY' 2>/dev/null` 里 split("<<") 得到 "'PY' 2>/dev/null"，
取第一个词 = `'PY'`（**带引号**），但真正的结束行是 `PY` →
`stripped == here_doc_end` 永远不成立 → heredoc 永不结束 →
后面所有行都被吞掉 → 卡在续行提示符，脚本被逐行拆散执行。
（`<<"PY"`、`<<\\PY`、`<<-PY` 同理。）
"""
import shutil

P = 'bin/source_cmd.py'
s = open(P, encoding='utf-8').read()

OLD = """                    here_doc_end = right.split()[0]"""
NEW = """                    # 去掉定界符的引号/转义：<<'PY' / <<"PY" / <<\\PY → PY
                    # （否则结束行永远匹配不上，heredoc 会吞掉后面所有行）
                    here_doc_end = right.split()[0].strip("'\\"\\\\")
                    if here_doc_end.startswith('-'):
                        here_doc_end = here_doc_end[1:]   # <<-EOF"""

n = s.count(OLD)
assert n == 1, ('occurrences', n)
shutil.copy(P, P + '.bak2')
open(P, 'w', encoding='utf-8').write(s.replace(OLD, NEW, 1))
print('HEREDOC QUOTE FIX APPLIED')
