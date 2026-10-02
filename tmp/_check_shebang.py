# -*- coding: utf-8 -*-
"""验证 resolve_path 对系统 bin 路径放行、对普通路径仍解析。"""
import sys
import os

sys.path.insert(0, '.')
from Onyx import resolve_path as R, _is_system_bin_path as S   # noqa: E402

cases = ['/usr/bin/env', '/bin/bash', '/usr/bin/python3', '/usr/local/bin/node',
         os.path.join(os.environ.get('PREFIX', '/usr'), 'bin', 'zsh')]
for p in cases:
    print('%-40s system_bin=%-6s resolve=%r' % (p, S(p), R(p)))

print('--- 普通路径仍走虚拟解析 ---')
print('tmp/_t1.sh ->', R('tmp/_t1.sh'))
print('~/x.sh     ->', R('~/x.sh'))
