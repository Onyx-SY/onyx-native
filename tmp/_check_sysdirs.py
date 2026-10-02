# -*- coding: utf-8 -*-
import sys
import os

sys.path.insert(0, '.')
from Onyx import resolve_path as R   # noqa: E402

for p in ['/proc', '/proc/net/tcp', '/dev/null', '/sys/class',
          '/run/x', '/usr/bin/env', '/bin/bash',
          os.path.join(os.environ.get('PREFIX', '/usr'), 'bin', 'zsh'),
          'tmp/x.sh', '~/a.sh']:
    print('%-42s -> %r' % (p, R(p)))
