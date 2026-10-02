# -*- coding: utf-8 -*-
"""脚本执行矩阵：各种脚本类型 × 各种 shebang，看 Onyx 生成的执行命令。"""
import sys
import os

sys.path.insert(0, '.')
from lib.parse import handle_executable_path, read_shebang   # noqa: E402

ROOT = os.getcwd()


def mk(name, content, execbit):
    p = os.path.join('tmp', name)
    with open(p, 'w') as f:
        f.write(content)
    os.chmod(p, 0o755 if execbit else 0o644)
    return p


def rp(x):
    return os.path.abspath(x) if x.startswith('.') else x


CASES = [
    ('sh-bash-shebang+x',  '#!/bin/bash\necho hi\n',                True),
    ('sh-env-bash+x',      '#!/usr/bin/env bash\necho hi\n',        True),
    ('sh-env-bash-x',      '#!/usr/bin/env bash\necho hi\n',        False),
    ('sh-env-py3+x',       '#!/usr/bin/env python3\nprint(1)\n',    True),
    ('sh-env-py3-x',       '#!/usr/bin/env python3\nprint(1)\n',    False),
    ('sh-noshebang+x',     'echo hi\n',                             True),
    ('sh-noshebang-x',     'echo hi\n',                             False),
    ('sh-abs-shebang+x',   '#!/data/data/com.termux/files/usr/bin/bash\necho hi\n', True),
    ('sh-zsh-shebang+x',   '#!/usr/bin/env zsh\necho hi\n',         True),
    ('bin-noext+x',        'echo hi\n',                             True),
]

print('%-22s %-28s %s' % ('用例', 'shebang', 'Onyx 生成的执行命令'))
print('-' * 100)
for name, content, x in CASES:
    p = mk(name, content, x)
    sb = read_shebang(p)
    out = handle_executable_path('./' + p, rp, ROOT)
    print('%-22s %-28s %s' % (name, (sb or '(无)')[:26], out))
