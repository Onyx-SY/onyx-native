# -*- coding: utf-8 -*-
"""把 shell 配置文件从「项目目录 etc/onyx/shell」迁到「用户主目录 ~/.onyx/shell」。

原因：项目目录在 Linux 下可能是只读安装（/usr/share、/opt、Nix store…），
写入会 Permission denied；用户主目录一定可写。
与 Onyx 既有约定一致（~/.onyx/ 已用于 cache、venv 等）。

统一路径：os.path.expanduser("~/.onyx/shell")
"""
import shutil

EDITS = [
    # 1) bin/manage.py
    ('bin/manage.py',
     'SHELL_CONFIG_PATH = os.path.join(ROOT_DIR, "etc", "onyx", "shell")',
     '# 放用户主目录：项目目录在 Linux 下可能只读（/usr/share、/opt、Nix…），\n'
     '# 写配置会 Permission denied。~/.onyx/ 与 Onyx 其他用户数据一致。\n'
     'SHELL_CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".onyx", "shell")'),
    # 2) lib/parse.py
    ('lib/parse.py',
     '_SHELL_TYPE_FILE = _os.path.join(\n'
     '    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),\n'
     '    "etc", "onyx", "shell")',
     '# 用户主目录（项目目录在 Linux 下可能只读 → 写配置会 Permission denied）\n'
     '_SHELL_TYPE_FILE = _os.path.join(\n'
     '    _os.path.expanduser("~"), ".onyx", "shell")'),
    # 3) lib/terminal/exe.py
    ('lib/terminal/exe.py',
     "        _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n"
     "        with open(os.path.join(_root, 'etc', 'onyx', 'shell'), encoding='utf-8') as _f:\n"
     "            _cfg_shell = _f.read().strip()",
     "        with open(os.path.join(os.path.expanduser('~'), '.onyx', 'shell'),\n"
     "                  encoding='utf-8') as _f:\n"
     "            _cfg_shell = _f.read().strip()"),
]

for path, old, new in EDITS:
    s = open(path, encoding='utf-8').read()
    n = s.count(old)
    assert n == 1, (path, n)
    shutil.copy(path, path + '.bak6')
    open(path, 'w', encoding='utf-8').write(s.replace(old, new, 1))
    print('patched:', path)
