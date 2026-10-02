# -*- coding: utf-8 -*-
"""复现 exe.py 的 zsh 启动方式，看 zsh 是否立即退出。"""
import os
import pty
import fcntl
import termios
import time
import select

Z = '/data/data/com.termux/files/usr/bin/zsh'
ARGS = [Z, '--norc', '--no-rcs', '--no-globalrcs', '-f']


def spawn(extra_env, label):
    master, slave = pty.openpty()
    pid = os.fork()
    if pid == 0:
        os.close(master)
        os.setsid()
        try:
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
        except Exception:
            pass
        os.dup2(slave, 0)
        os.dup2(slave, 1)
        os.dup2(slave, 2)
        if slave > 2:
            os.close(slave)
        env = os.environ.copy()
        for k in ('PS1', 'PS2', 'PS3', 'PS4', 'PROMPT', 'PROMPT_COMMAND',
                  'RPROMPT', 'RPS1'):
            env.pop(k, None)
        env.update(extra_env)
        os.execvpe(Z, ARGS, env)
        os._exit(1)
    os.close(slave)
    time.sleep(1.0)
    state = '?'
    try:
        with open('/proc/%d/stat' % pid, 'rb') as f:
            raw = f.read()
        state = raw[raw.rfind(b')') + 2:].split()[0].decode()
    except Exception as e:
        state = 'GONE(%s)' % e
    out = b''
    r, _, _ = select.select([master], [], [], 0.5)
    if master in r:
        try:
            out = os.read(master, 3000)
        except OSError:
            pass
    print('[%s] state=%s out=%r' % (label, state, out[:220]))
    try:
        os.killpg(os.getpgid(pid), 9)
    except Exception:
        pass
    os.close(master)


spawn({'TERM': 'xterm-256color'}, 'minimal')
spawn({'TERM': 'xterm-256color', 'PS1': '', 'PROMPT': '$P$G',
       'ZDOTDIR': '/dev/null', 'HISTFILE': '/dev/null',
       'HISTSIZE': '0', 'SAVEHIST': '0'}, 'like-exe.py')
