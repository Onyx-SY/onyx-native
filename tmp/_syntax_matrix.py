# -*- coding: utf-8 -*-
"""bash 语法解释正确性 × 各 shell 适配矩阵。

对每个用例跑关键解析函数，检查在 bash 下是否正确、在其他 shell 下是否
走对分支（不崩、不误判）。
"""
import sys
import os

sys.path.insert(0, '.')
from lib import parse as P   # noqa: E402

SHELLS = ['bash', 'zsh', 'fish', 'sh', 'cmd', 'powershell']

CASES = [
    ('双引号',        'echo "a b"'),
    ('单引号',        "echo 'a b'"),
    ('引号内含引号',  '''echo "it's"'''),
    ('转义空格',      'echo a\\ b'),
    ('变量',          'echo $VAR'),
    ('花括号变量',    'echo ${VAR}'),
    ('命令替换$()',   'echo $(date)'),
    ('命令替换``',    'echo `date`'),
    ('管道',          'ls | grep x'),
    ('引号内管道',    'ls "a|b"'),
    ('逻辑操作符',    'a && b || c'),
    ('引号内&&',      'echo "a && b"'),
    ('重定向',        'echo x > f'),
    ('追加重定向',    'echo x >> f'),
    ('引号内>',       'echo ">"'),
    ('fd重定向',      'echo x 2>&1'),
    ('分号',          'a; b; c'),
    ('引号内;',       'echo "a;b"'),
    ('花括号展开',    'echo {1..3}'),
    ('列表展开',      'echo {a,b}'),
    ('注释',          'echo x # comment'),
    ('引号内#',       'echo "# no"'),
    ('子shell',       '(cd /x && ls)'),
    ('if结构',        'if true; then echo y; fi'),
    ('heredoc',       'cat <<EOF\nhi\nEOF'),
]

print('%-14s %-22s %-6s %-6s %-6s %-6s %-6s %-6s' % (
    '用例', '输入', 'bash', 'zsh', 'fish', 'sh', 'cmd', 'pwsh'))
print('-' * 100)

for name, text in CASES:
    row = []
    for sh in SHELLS:
        try:
            parts = P.smart_shlex_split(text, sh)
            n = len(parts)
        except Exception as e:                       # noqa: BLE001
            n = 'ERR'
        row.append(str(n))
    # 管道 / 逻辑 / 重定向 判断（仅 bash 与 zsh 有意义）
    extra = ''
    try:
        extra = 'pipe=%s logic=%s redir=%s' % (
            P.has_pipeline(text, 'bash'),
            P.has_logical_operators(text, 'bash'),
            P.has_redirect(text, 'bash'))
    except Exception as e:                           # noqa: BLE001
        extra = 'ERR:%s' % e
    print('%-14s %-22s %-6s %-6s %-6s %-6s %-6s %-6s  %s' % (
        name, repr(text)[:20], row[0], row[1], row[2], row[3], row[4], row[5], extra))
