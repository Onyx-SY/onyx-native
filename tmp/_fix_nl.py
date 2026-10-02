# -*- coding: utf-8 -*-
"""修「有时多一个换行」：静默 flush 已把输出放完后，哨兵到达时 _head 为空，
旧逻辑仍无条件补 '\\n' → 凭空多一个空行。改为按「上次转发内容的末字节」判断。"""
p = 'lib/terminal/exe.py'
s = open(p, encoding='utf-8').read()


def rep(a, b, cnt=1):
    global s
    n = s.count(a)
    assert n == cnt, (a[:60], n)
    s = s.replace(a, b)


# 1) 新增「上次转发末字节」跟踪（用 _SENT_KEEP 那两行唯一定位）
rep("""            _SENT_KEEP = 40   # 哨兵最长约 30B（含 CRLF）；越小转发越实时
            _scan_bytes = bytearray()""",
    """            _SENT_KEEP = 40   # 哨兵最长约 30B（含 CRLF）；越小转发越实时
            _scan_bytes = bytearray()
            _last_out_byte = [None]   # 最近一次已转发内容的最后一个字节""")

# 2) 哨兵命中分支：_head 为空时按上次末字节决定是否补换行
rep("""                    _head = _buf[:_cut]
                    if not _head.endswith(b'\\n'):
                        _head += b'\\n'    # 输出未以换行结束 → 补一个，避免与提示符粘连
                    _scan_bytes = bytearray(_buf[_m.end():])
                    if _head:
                        _emit(_head)""",
    """                    _head = _buf[:_cut]
                    if _head:
                        if not _head.endswith(b'\\n'):
                            _head += b'\\n'   # 输出未以换行结束 → 补一个
                    elif _last_out_byte[0] not in (None, b'\\n'):
                        _head = b'\\n'        # 上次输出没换行结尾 → 补一个，避免粘连
                    _scan_bytes = bytearray(_buf[_m.end():])
                    if _head:
                        _emit(_head)
                        _last_out_byte[0] = _head[-1:]""")

# 3) KEEP 转发分支：同步记录末字节
rep("""                    _head = _buf[:len(_buf) - _SENT_KEEP]
                    _scan_bytes = bytearray(_buf[len(_buf) - _SENT_KEEP:])
                    _emit(_head)""",
    """                    _head = _buf[:len(_buf) - _SENT_KEEP]
                    _scan_bytes = bytearray(_buf[len(_buf) - _SENT_KEEP:])
                    _emit(_head)
                    _last_out_byte[0] = _head[-1:]""")

# 4) 静默 flush：记录末字节 + 补写 output_buffer（原先漏了）
rep("""                if _scan_bytes and _now - _last_activity > 0.015:
                    _emit(bytes(_scan_bytes))
                    _scan_bytes = bytearray()""",
    """                if _scan_bytes and _now - _last_activity > 0.015:
                    _b = bytes(_scan_bytes)
                    _emit(_b)
                    _last_out_byte[0] = _b[-1:]
                    if output_buffer is not None:
                        output_buffer.append(
                            _safe(_b.decode('utf-8', 'replace').replace('\\r\\n', '\\n')))
                    _scan_bytes = bytearray()""")

open(p, 'w', encoding='utf-8').write(s)
print('NL FIX APPLIED')
