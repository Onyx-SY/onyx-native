# -*- coding: utf-8 -*-
"""修复 TUI 交互异常：①首屏被扣住（要按回车才渲染）②命令后多一个换行。"""
p = 'lib/terminal/exe.py'
s = open(p, encoding='utf-8').read()


def rep(a, b, cnt=1):
    global s
    n = s.count(a)
    assert n == cnt, (a[:60], n)
    s = s.replace(a, b)


# ① 保留区过大 → TUI 首屏末尾 200 字节被扣住，必须等用户按键产生新输出才放出。
#    哨兵最长约 35 字节，48 足够。
rep("            _SENT_KEEP = 200",
    "            _SENT_KEEP = 48   # 哨兵最长约 35B，留余量即可（过大 → TUI 首屏被扣住）")

# ② 静默 50ms 就把扫描缓冲全部放出（TUI 输出是阵发的，静默即说明不是哨兵中间）
rep("""                # --- 空闲判定 / 硬超时兜底（绝不永久阻塞）---
                _now = time.time()""",
    """                # --- 空闲判定 / 硬超时兜底（绝不永久阻塞）---
                _now = time.time()
                # 静默 50ms → 扫描缓冲全部放出：TUI 首屏（nano/vim）不能被
                # 「等哨兵」的保留区扣住，否则要等用户按键才渲染。
                if _scan_bytes and _now - _last_activity > 0.05:
                    _emit(bytes(_scan_bytes))
                    _scan_bytes = bytearray()""")

# ③ 哨兵的 "\\n" 经 PTY ONLCR 变 "\\r\\n"：匹配点落在 '\\n' 时，前一个 '\\r'
#    属于哨兵 → 一并切掉，否则 _head 以 '\\r' 结尾会被判定为「缺换行」→ 多一空行。
rep("""                if _m:
                    _head = _buf[:_m.start()]
                    # 哨兵自带一个前导 \\n（保证独占一行）。若前面的输出本身
                    # 已以 \\n 结尾就不要再补，否则会多出一个空行（旧实现无条件
                    # +1 → 每条命令后凭空多一空行）。
                    if not _head.endswith(b'\\n'):
                        _head += b'\\n'
                    _scan_bytes = bytearray(_buf[_m.end():])""",
    """                if _m:
                    _cut = _m.start()
                    if _cut > 0 and _buf[_cut - 1:_cut] == b'\\r':
                        _cut -= 1          # 哨兵的 CR 也切掉，避免多一个空行
                    _head = _buf[:_cut]
                    if not _head.endswith(b'\\n'):
                        _head += b'\\n'    # 输出未以换行结束 → 补一个，避免与提示符粘连
                    _scan_bytes = bytearray(_buf[_m.end():])""")

# ④ echo 跳过超时改回 0.2s（0.6s 会让 TUI 首屏被扣住更久）
rep("_ECHO_SKIP_TIMEOUT = float(os.environ.get('ONYX_ECHO_TIMEOUT', '0.6'))",
    "_ECHO_SKIP_TIMEOUT = float(os.environ.get('ONYX_ECHO_TIMEOUT', '0.2'))")

# ⑤ 回显缓冲里一旦出现转义序列 → 绝不是命令回显（TUI / 彩色输出）→ 立即放出
rep("""                        else:
                            _echo_buf += data
                            _max_echo_len = len(cmd.encode('utf-8', 'replace')) + 64""",
    """                        elif b'\\x1b' in _echo_buf + data:
                            # 含 ESC → 是 TUI/彩色输出而非命令回显 → 立即放出（零延迟）
                            _emit_bytes = _echo_buf + data
                            _echo_buf = b""
                            _echo_pending = False
                        else:
                            _echo_buf += data
                            _max_echo_len = len(cmd.encode('utf-8', 'replace')) + 64""")

open(p, 'w', encoding='utf-8').write(s)
print('TUI FIXES APPLIED')
