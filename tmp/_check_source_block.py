# -*- coding: utf-8 -*-
"""验证 source 脚本的分块：{ } / 函数 / 带引号 heredoc 是否整块提交。"""
import sys
import types

sys.path.insert(0, '.')

CALLS = []
_fake = types.ModuleType('Onyx')
_fake.parse_and_execute = lambda c, **k: CALLS.append(c)
_fake.Fore = types.SimpleNamespace(RED='', YELLOW='', GREEN='', CYAN='')
_fake.Style = types.SimpleNamespace(RESET_ALL='')
_fake.global_config = {'display_info': {'language': {'current': 'chinese'}}}
sys.modules['Onyx'] = _fake

from bin.source_cmd import _source_onyx_script   # noqa: E402

SCRIPT = (
    "#!/bin/bash\n"
    "PORT=8080\n"
    "log() {\n"
    "  echo port=$PORT\n"
    "}\n"
    "cat <<PY\n"
    "hello\n"
    "PY\n"
    "python3 - <<'PY2' 2>/dev/null\n"
    "print(1)\n"
    "PY2\n"
    "echo after\n"
)

with open('tmp/_blk.sh', 'w') as f:
    f.write(SCRIPT)

_source_onyx_script('tmp/_blk.sh', 'test')
print('提交块数:', len(CALLS))
for i, c in enumerate(CALLS):
    print('--- 块 %d ---' % i)
    print(c)
