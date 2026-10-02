#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""哨兵（命令完成标记）机制可行性实验。

目标：验证「不依赖 PS1 的哨兵」是否可靠 ——
  A. bash: PROMPT_COMMAND 打印哨兵，用户改 PS1 后哨兵是否仍在？
  B. zsh : precmd 打印哨兵，用户改 PROMPT 后哨兵是否仍在？
  C. fish: fish_prompt 打印哨兵（fish 无 PS1 概念）
  D. 用 tcgetpgrp 区分「shell 在等输入」vs「子程序占前台」
  E. 哨兵跨 read 边界是否会被切开（分块读取风险）
"""
import os
import pty
import re
import select
import struct
import termios
import time
import fcntl
import shutil

MARK = "__DONE_TESTMARK__"


def spawn(argv, extra_env):
    master, slave = pty.openpty()
    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 80, 0, 0))
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
        for k in ('PS1', 'PROMPT', 'PROMPT_COMMAND', 'LINES', 'COLUMNS'):
            env.pop(k, None)
        env['TERM'] = 'xterm-256color'
        env.update(extra_env)
        try:
            os.execvpe(argv[0], argv, env)
        except Exception:
            os._exit(1)
    os.close(slave)
    return master, pid


_QUERY_PATS = (
    (re.compile(rb"\x1b\[0c"), b"\x1b[?62;22c"),                       # DA1
    (re.compile(rb"\x1b\]11;\?"), b"\x1b]11;rgb:0000/0000/0000\x1b\\"),  # 背景色
    (re.compile(rb"\x1b\[\?u"), b"\x1b[?0u"),                           # kitty keyboard
    (re.compile(rb"\x1b\[>0q"), b"\x1bP>|onyx-probe\x1b\\"),             # XTVERSION
)


def _answer_queries(master, buf):
    """回应终端能力查询（fish 4.x 启动时会问，不答就卡住）。"""
    for pat, resp in _QUERY_PATS:
        if pat.search(buf):
            try:
                os.write(master, resp)
            except OSError:
                pass
    m = re.search(rb"\x1bP\+q([0-9a-fA-F]+)\x1b\\", buf)   # XTGETTCAP
    if m:
        try:
            os.write(master, b"\x1bP0+r" + m.group(1) + b"\x1b\\")
        except OSError:
            pass


def drain(master, timeout=1.0):
    """读干净所有 pending 输出。"""
    buf = b""
    end = time.time() + timeout
    while time.time() < end:
        r, _, _ = select.select([master], [], [], 0.1)
        if master in r:
            try:
                d = os.read(master, 4096)
            except OSError:
                break
            if not d:
                break
            buf += d
            _answer_queries(master, buf)
            end = time.time() + 0.15
    return buf


def send(master, s):
    os.write(master, s.encode())


def probe(master, cmd, timeout=2.0):
    """发命令，读到哨兵为止，返回 (是否看到哨兵, 原始输出, 分块数)。"""
    send(master, cmd + "\n")
    buf = b""
    chunks = 0
    end = time.time() + timeout
    pat = re.compile(re.escape(MARK) + r":(-?\d+)")
    while time.time() < end:
        r, _, _ = select.select([master], [], [], 0.05)
        if master in r:
            try:
                d = os.read(master, 4096)
            except OSError:
                break
            if not d:
                break
            chunks += 1
            buf += d
            if pat.search(buf.decode('utf-8', 'replace')):
                return True, buf, chunks
    return False, buf, chunks


def fg_pgid(master):
    try:
        return os.tcgetpgrp(master)
    except Exception as e:
        return f"ERR:{e}"


def shell_pgid(pid):
    try:
        return os.getpgid(pid)
    except Exception as e:
        return f"ERR:{e}"


def section(title):
    print("\n" + "=" * 68)
    print(title)
    print("=" * 68)


def test_bash():
    section("A. bash：哨兵放 PROMPT_COMMAND（PS1 只放冗余哨兵）")
    if not shutil.which('bash'):
        print("  跳过：无 bash")
        return
    master, pid = spawn(['bash', '--norc', '--noprofile'], {
        'PS1': f'{MARK}:$?\\n',
        'PROMPT_COMMAND': f"printf '{MARK}:%s\\n' \"$?\"",
    })
    drain(master)
    print(f"  shell pid={pid} pgid={shell_pgid(pid)}  fg_pgid(master)={fg_pgid(master)}")

    ok, out, n = probe(master, "echo hello")
    print(f"  1) 初始:             哨兵={'有' if ok else '无'}  chunks={n}")
    print(f"     输出尾部: {out[-90:]!r}")

    probe(master, "PS1='$ '")            # 用户/AI 把 PS1 改成普通提示符
    ok, out, n = probe(master, "echo after-ps1-change")
    print(f"  2) PS1='$ ' 之后:    哨兵={'有' if ok else '无'}  chunks={n}   <== 关键")
    print(f"     输出尾部: {out[-90:]!r}")

    probe(master, "PS1='\\[\\033[32m\\]\\w\\[\\033[0m\\]$ '")   # 带颜色的花哨 PS1
    ok, out, n = probe(master, "echo after-color-ps1")
    print(f"  3) 彩色 PS1 之后:    哨兵={'有' if ok else '无'}  chunks={n}")
    print(f"     输出尾部: {out[-90:]!r}")

    probe(master, "PS1=''")              # PS1 清空
    ok, out, n = probe(master, "echo after-empty-ps1")
    print(f"  4) PS1='' 之后:      哨兵={'有' if ok else '无'}  chunks={n}")
    print(f"     输出尾部: {out[-90:]!r}")

    probe(master, "PROMPT_COMMAND=''")   # 连 PROMPT_COMMAND 也被清（最坏情况）
    ok, out, n = probe(master, "echo after-pc-cleared")
    print(f"  5) PROMPT_COMMAND='':哨兵={'有' if ok else '无'}  chunks={n}   <== 双保险都失效")

    os.killpg(os.getpgid(pid), 9)
    os.close(master)


def test_zsh():
    section("B. zsh：哨兵放 precmd_functions")
    if not shutil.which('zsh'):
        print("  跳过：无 zsh")
        return
    master, pid = spawn(['zsh', '-f'], {
        'PROMPT': '',
        'ZDOTDIR': '/dev/null',
    })
    drain(master)
    send(master, f"precmd() {{ printf '\\n{MARK}:%s\\n' $? }}\n")
    drain(master)
    print(f"  shell pid={pid} pgid={shell_pgid(pid)}  fg_pgid(master)={fg_pgid(master)}")

    ok, out, n = probe(master, "echo hello")
    print(f"  1) 初始:           哨兵={'有' if ok else '无'}  chunks={n}")
    print(f"     输出尾部: {out[-90:]!r}")

    probe(master, "PROMPT='$ '")
    ok, out, n = probe(master, "echo after-prompt-change")
    print(f"  2) PROMPT='$ ' 后: 哨兵={'有' if ok else '无'}  chunks={n}   <== 关键")

    os.killpg(os.getpgid(pid), 9)
    os.close(master)


def test_fish():
    section("C. fish：哨兵放 fish_prompt")
    if not shutil.which('fish'):
        print("  跳过：无 fish")
        return
    master, pid = spawn(['fish', '--no-config', '--private'], {})
    raw = drain(master, 3.0)
    print(f"  启动输出({len(raw)}B): {raw[-140:]!r}")
    send(master, f"function fish_prompt; printf '\\n{MARK}:%s\\n' $status; end\n")
    raw2 = drain(master, 2.0)
    print(f"  设置 fish_prompt 后({len(raw2)}B): {raw2[-140:]!r}")
    ok, out, n = probe(master, "echo hello", timeout=4.0)
    print(f"  1) 初始: 哨兵={'有' if ok else '无'}  chunks={n}")
    print(f"     输出尾部: {out[-120:]!r}")
    os.killpg(os.getpgid(pid), 9)
    os.close(master)


def test_fg_detect():
    section("D. tcgetpgrp：区分「shell 等输入」vs「子程序占前台」")
    master, pid = spawn(['bash', '--norc', '--noprofile'], {
        'PS1': f'{MARK}:$?\\n',
        'PROMPT_COMMAND': f"printf '{MARK}:%s\\n' \"$?\"",
    })
    drain(master)
    spg = shell_pgid(pid)
    print(f"  shell pgid = {spg}")

    probe(master, "echo idle")
    print(f"  空闲时        fg_pgid={fg_pgid(master)}  == shell? {fg_pgid(master) == spg}")

    send(master, "sleep 5\n")
    time.sleep(0.5)
    print(f"  sleep 运行时  fg_pgid={fg_pgid(master)}  == shell? {fg_pgid(master) == spg}")
    print("     ^ 前台 pgid != shell pgid → 有子程序占前台，不该超时干预")

    send(master, "\x03")
    time.sleep(0.4)
    drain(master)
    print(f"  Ctrl+C 之后   fg_pgid={fg_pgid(master)}  == shell? {fg_pgid(master) == spg}")

    os.killpg(os.getpgid(pid), 9)
    os.close(master)


def test_split():
    section("E. 哨兵跨 read 边界：现有代码只对「单次 read 的 text」做正则")
    print("  现有 _execute_passthrough 第 926-928 行：")
    print("      done_match = re.search(done_pattern, text)   # text = 单次 read 的数据")
    print("  若 marker 恰好被 4096 分块切开 → 单次匹配失败 → 永久阻塞（无超时）。")
    stream = "some output\r\n__DONE_abc123:0__\r\n"
    cut = stream.index("__DONE_") + 4
    a, b = stream[:cut], stream[cut:]
    pat = re.compile(r"__DONE_([0-9a-f]+):(-?\d+)")
    print(f"  第一块 {a!r} -> match={bool(pat.search(a))}")
    print(f"  第二块 {b!r} -> match={bool(pat.search(b))}")
    print(f"  累积后        -> match={bool(pat.search(a + b))}   <== 必须用累积缓冲")


if __name__ == '__main__':
    test_bash()
    test_zsh()
    test_fish()
    test_fg_detect()
    test_split()
    print("\n完成。")
