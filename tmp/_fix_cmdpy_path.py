# -*- coding: utf-8 -*-
"""修无 shebang 脚本的执行命令：cmd.py 路径多拼了一层 'onyx/'。

实测生成的是  python <root>/onyx/onyx/onyx/cmd.py -c "source ..."
而 cmd.py 实际在   <root>/onyx/onyx/cmd.py   → 必然执行失败。
改为按存在性探测候选路径。
"""
import shutil

P = 'lib/parse.py'
shutil.copy(P, P + '.bak2')
s = open(P, encoding='utf-8').read()

OLD = """                if virtual_root_dir:
                    cmd_py_path = os.path.join(virtual_root_dir, 'onyx', 'cmd.py')"""
NEW = """                if virtual_root_dir:
                    # 虚拟根可能是项目根，也可能是项目根的父目录 →
                    # 按存在性探测，避免拼出 <root>/onyx/onyx/cmd.py 这种错路径。
                    cmd_py_path = None
                    for _cand in (os.path.join(virtual_root_dir, 'cmd.py'),
                                  os.path.join(virtual_root_dir, 'onyx', 'cmd.py')):
                        if os.path.isfile(_cand):
                            cmd_py_path = _cand
                            break
                    if cmd_py_path is None:
                        cmd_py_path = os.path.join(virtual_root_dir, 'onyx', 'cmd.py')"""

assert s.count(OLD) == 1, s.count(OLD)
s = s.replace(OLD, NEW, 1)
open(P, 'w', encoding='utf-8').write(s)
print('CMDPY PATH FIX APPLIED')
