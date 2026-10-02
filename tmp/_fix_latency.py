# -*- coding: utf-8 -*-
"""降低 TUI 输出延迟：① 有待放字节时用短轮询 ② 静默窗口 50ms→15ms ③ KEEP 48→40。"""
p = 'lib/terminal/exe.py'
s = open(p, encoding='utf-8').read()


def rep(a, b, cnt=1):
    global s
    n = s.count(a)
    assert n == cnt, (a[:60], n)
    s = s.replace(a, b)


rep("            _SENT_KEEP = 48   # 哨兵最长约 35B，留余量即可（过大 → TUI 首屏被扣住）",
    "            _SENT_KEEP = 40   # 哨兵最长约 30B（含 CRLF）；越小转发越实时")

rep("            _POLL_T = 0.25",
    "            _POLL_T = 0.25          # 空闲轮询（省 CPU）\n"
    "            _POLL_T_PENDING = 0.01  # 扫描缓冲还有待放字节时的轮询（低延迟）")

rep("                        rlist, _, _ = select.select(_watch_fds, [], [], _POLL_T)",
    "                        rlist, _, _ = select.select(\n"
    "                            _watch_fds, [], [],\n"
    "                            _POLL_T_PENDING if _scan_bytes else _POLL_T)")

rep("""                # 静默 50ms → 扫描缓冲全部放出：TUI 首屏（nano/vim）不能被
                # 「等哨兵」的保留区扣住，否则要等用户按键才渲染。
                if _scan_bytes and _now - _last_activity > 0.05:""",
    """                # 静默 15ms → 扫描缓冲全部放出：TUI 首屏（nano/vim）不能被
                # 「等哨兵」的保留区扣住，否则要等用户按键才渲染。
                # （配合上面的 _POLL_T_PENDING，最坏延迟 ≈ 15ms。）
                if _scan_bytes and _now - _last_activity > 0.015:""")

open(p, 'w', encoding='utf-8').write(s)
print('LATENCY FIX APPLIED')
