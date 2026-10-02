# -*- coding: utf-8 -*-
"""验证 shell 配置已迁到 ~/.onyx/shell，且三方读取一致。"""
import os
import sys
import shutil

sys.path.insert(0, '.')
os.environ.pop('ONYX_SHELL', None)

from lib.parse import get_configured_shell_type as G   # noqa: E402
from lib.terminal import exe                           # noqa: E402

cfg = os.path.join(os.path.expanduser('~'), '.onyx', 'shell')
backup = None
if os.path.exists(cfg):
    backup = open(cfg, encoding='utf-8').read()

try:
    os.makedirs(os.path.dirname(cfg), exist_ok=True)
    with open(cfg, 'w') as f:
        f.write((shutil.which('zsh') or shutil.which('bash')) + '\n')
    exe._shell_cache = None
    exe._shell_override = None
    print('配置文件路径 :', cfg)
    print('输入层 shell  :', G())
    print('PTY    shell  :', os.path.basename(exe.get_shell()))
    print('一致          :', G() == os.path.basename(exe.get_shell()).lower())
finally:
    if backup is not None:
        with open(cfg, 'w') as f:
            f.write(backup)
    else:
        try:
            os.remove(cfg)
        except OSError:
            pass
    exe._shell_cache = None
    exe._shell_override = None
    print('已还原')
