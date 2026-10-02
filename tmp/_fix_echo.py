# -*- coding: utf-8 -*-
"""修正回显跳过：① 上一版加的 ESC 立即放行分支会误吞（shell 的 \\x1b[?2004l 就在
回显前面）→ 删掉；② 改为「剥掉 ANSI 序列后再比较回显行」。"""
p = 'lib/terminal/exe.py'
s = open(p, encoding='utf-8').read()


def rep(a, b, cnt=1):
    global s
    n = s.count(a)
    assert n == cnt, (a[:60], n)
    s = s.replace(a, b)


# 加 ANSI 剥离正则
rep("_SHELL_READY_TIMEOUT = float(os.environ.get('ONYX_SHELL_READY_TIMEOUT', '5'))",
    "_SHELL_READY_TIMEOUT = float(os.environ.get('ONYX_SHELL_READY_TIMEOUT', '5'))\n"
    "# 回显行里可能夹着 shell 的控制序列（\\x1b[?2004l 等），比较前需剥掉\n"
    "_ANSI_RE = re.compile(rb'\\x1b\\[[0-9;?]*[a-zA-Z]|\\x1b\\][^\\x07]*\\x07|\\x1b[=>]')")

# 删掉会误触发的 ESC 分支
rep("""                        elif b'\\x1b' in _echo_buf + data:
                            # 含 ESC → 是 TUI/彩色输出而非命令回显 → 立即放出（零延迟）
                            _emit_bytes = _echo_buf + data
                            _echo_buf = b""
                            _echo_pending = False
                        else:
""", """                        else:
""")

# 剥掉 ANSI 再比回显行
rep("                                _echo_line = _echo_buf[:_nl].strip()",
    "                                _echo_line = _ANSI_RE.sub(b'', _echo_buf[:_nl]).strip()")

open(p, 'w', encoding='utf-8').write(s)
print('ECHO FIX APPLIED')
