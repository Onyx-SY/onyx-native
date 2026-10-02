# -*- coding: utf-8 -*-
"""修 zsh 启动：zsh 没有 --norc（那是 bash 的选项），传了会直接退出
→ PersistentShell(shell_path=zsh) 立即 dead。"""
import shutil

P = 'lib/terminal/exe.py'
shutil.copy(P, P + '.bak2')
s = open(P, encoding='utf-8').read()

OLD = """                if self.shell_name == 'zsh':
                    os.execvpe(self.shell, [
                        self.shell,
                        '--norc',
                        '--no-rcs',
                        '--no-globalrcs',
                        '-f'  # no startup files
                    ], env)"""
NEW = """                if self.shell_name == 'zsh':
                    # 注意：zsh 没有 --norc（那是 bash 的选项）——传了会
                    # "no such option: norc" 直接退出。只用 zsh 自己的开关。
                    os.execvpe(self.shell, [
                        self.shell,
                        '--no-rcs',
                        '--no-globalrcs',
                        '-f'  # no startup files
                    ], env)"""
assert s.count(OLD) == 1
s = s.replace(OLD, NEW, 1)

# Windows 分支的 shell_args 也顺带修（zsh 不能走 bash 的 --norc/--noprofile）
OLD2 = """        else:
            # For WSL/bash on Windows
            shell_args = [self.shell, '--norc', '--noprofile']"""
NEW2 = """        elif self.shell_name == 'zsh':
            shell_args = [self.shell, '--no-rcs', '--no-globalrcs', '-f']
        else:
            # For WSL/bash on Windows
            shell_args = [self.shell, '--norc', '--noprofile']"""
assert s.count(OLD2) == 1
s = s.replace(OLD2, NEW2, 1)

open(P, 'w', encoding='utf-8').write(s)
print('ZSH ARGS FIXED')

# 顺带修测试脚本：_t2.sh 依赖 _t1.sh 的变量，需先 source
T = 'tmp/_t2.sh'
c = open(T, encoding='utf-8').read()
if not c.startswith('source tmp/_t1.sh'):
    open(T, 'w', encoding='utf-8').write('source tmp/_t1.sh\n' + c)
    print('_t2.sh FIXED')
