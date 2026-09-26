# -*- coding: utf-8 -*-
"""AI 对话模式：把 ptk 键绑定改为 keymap 注册表驱动。"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── 1) keymap：拆出「补全上一项」（shift+tab 与 ctrl+i 是不同动作）──
KP = os.path.join(ROOT, "bin", "ai_lib", "keymap.py")
ks = io.open(KP, encoding="utf-8").read()
OLD_ACT = ('    ("repl.complete", "repl", "触发补全", "Trigger completion", '
           '["ctrl+i", "shift+tab"]),')
NEW_ACT = ('    ("repl.complete", "repl", "触发补全 / 下一项", "Complete / next", ["ctrl+i"]),\n'
           '    ("repl.complete_prev", "repl", "补全上一项", "Complete previous", ["shift+tab"]),')
if NEW_ACT in ks:
    print("SKIP keymap: 已存在")
elif ks.count(OLD_ACT) == 1:
    ks = ks.replace(OLD_ACT, NEW_ACT, 1)
    tmp = KP + ".tmp"
    io.open(tmp, "w", encoding="utf-8").write(ks)
    os.replace(tmp, KP)
    print("OK   keymap: 拆分 repl.complete / repl.complete_prev")
else:
    print("FAIL keymap: 锚点未命中")
    sys.exit(1)

# ── 2) ai_interactive：注册表驱动绑定 ──
P = os.path.join(ROOT, "bin", "ai_interactive.py")
src = io.open(P, encoding="utf-8").read()

HELPER = '''    # ── 键绑定：Enter 提交发送；Alt+Enter 进入/退出多行模式 ──
    # 注：Ctrl+Enter 在终端协议层面与 Enter 字节相同（都是 \\r），prompt_toolkit 无法区分，
    #     因此多行切换键只能选 Alt+Enter（escape-enter）。
    # merge 默认绑定（保留全部 readline 编辑功能：方向键/Ctrl+A/E/U/K/Tab/Ctrl+R）
    _kb = KeyBindings()

    # ── 键位来自 keymap 注册表（用户可在 /config → ⌨️ 按键设置 里改）──
    try:
        from bin.ai_lib import keymap as _km
        _km.init(user_home_dir)
    except Exception:
        _km = None

    def _add_binding(action_id, handler, **kw):
        """把 handler 绑到注册表里该动作的全部键上（支持一键多义与多键序列）。"""
        if _km is None:
            return
        for _combo in _km.combos(action_id):
            _keys = _km.to_ptk(_combo)
            if not _keys:
                continue
            try:
                _kb.add(*_keys, **kw)(handler)
            except Exception:
                continue
'''

OLD_HEAD = '''    # ── 键绑定：Enter 提交发送；Alt+Enter 进入/退出多行模式 ──
    # 注：Ctrl+Enter 在终端协议层面与 Enter 字节相同（都是 \\r），prompt_toolkit 无法区分，
    #     因此多行切换键只能选 Alt+Enter（escape-enter）。
    # merge 默认绑定（保留全部 readline 编辑功能：方向键/Ctrl+A/E/U/K/Tab/Ctrl+R）
    _kb = KeyBindings()
'''

OLD_SUBMIT = """    @_kb.add('enter', eager=True, filter=~is_searching)
    @_kb.add('c-j', eager=True, filter=~is_searching)
    def _submit(event):"""
NEW_SUBMIT = """    def _submit(event):"""

OLD_SUBMIT_END = """        event.current_buffer.validate_and_handle()

    @_kb.add('escape', 'enter', eager=True, filter=~is_searching)
    @_kb.add('escape', 'c-j', eager=True, filter=~is_searching)
    def _newline(event):"""
NEW_SUBMIT_END = """        event.current_buffer.validate_and_handle()

    _add_binding("repl.submit", _submit, eager=True, filter=~is_searching)

    def _newline(event):"""

OLD_NL_END = """            b.insert_text('\\n')

    # ── Tab 补全：对齐 lib/terminal/kb.py（completion_next / completion_prev）──"""
NEW_NL_END = """            b.insert_text('\\n')

    _add_binding("repl.newline", _newline, eager=True, filter=~is_searching)

    # ── Tab 补全：对齐 lib/terminal/kb.py（completion_next / completion_prev）──"""

OLD_CN = """    @_kb.add('c-i', eager=True, filter=~is_searching)
    def _complete_next(event):"""
NEW_CN = """    def _complete_next(event):"""

OLD_CN_END = """            b.start_completion(select_first=False)

    @_kb.add('s-tab', eager=True, filter=~is_searching)
    def _complete_prev(event):"""
NEW_CN_END = """            b.start_completion(select_first=False)

    _add_binding("repl.complete", _complete_next, eager=True, filter=~is_searching)

    def _complete_prev(event):"""

OLD_CP_END = """            b.start_completion(select_first=False)

    # ── →（右方向键）：有虚影直接接受（对齐 lib/terminal/kb.py）──"""
NEW_CP_END = """            b.start_completion(select_first=False)

    _add_binding("repl.complete_prev", _complete_prev, eager=True, filter=~is_searching)

    # ── →（右方向键）：有虚影直接接受（对齐 lib/terminal/kb.py）──"""

OLD_CC = """    @_kb.add('c-c', eager=True, filter=~is_searching)
    def _cancel(event):"""
NEW_CC = """    def _cancel(event):"""

OLD_CC_END = """        event.current_buffer.reset()

    @_kb.add('escape', filter=~is_searching)
    def _esc_exit(event):"""
NEW_CC_END = """        event.current_buffer.reset()

    _add_binding("repl.cancel", _cancel, eager=True, filter=~is_searching)

    # 注：ESC（单独按）退出对话、`/` 弹命令菜单、`right` 接受虚影属于结构性按键，
    #     不进注册表（改了会破坏 readline/菜单语义）。
    @_kb.add('escape', filter=~is_searching)
    def _esc_exit(event):"""

OLD_CD = """    @_kb.add('c-d', filter=~is_searching & _buffer_empty)
    def _eof(event):"""
NEW_CD = """    def _eof(event):"""

OLD_CD_END = """        event.app.exit(exception=EOFError)

    # 顺序注意：defaults 在前、_kb 在后"""
NEW_CD_END = """        event.app.exit(exception=EOFError)

    _add_binding("repl.quit", _eof, filter=~is_searching & _buffer_empty)

    # 顺序注意：defaults 在前、_kb 在后"""

pairs = [("head", OLD_HEAD, HELPER), ("submit", OLD_SUBMIT, NEW_SUBMIT),
         ("submit_end", OLD_SUBMIT_END, NEW_SUBMIT_END),
         ("nl_end", OLD_NL_END, NEW_NL_END),
         ("complete_next", OLD_CN, NEW_CN), ("cn_end", OLD_CN_END, NEW_CN_END),
         ("cp_end", OLD_CP_END, NEW_CP_END),
         ("cancel", OLD_CC, NEW_CC), ("cc_end", OLD_CC_END, NEW_CC_END),
         ("eof", OLD_CD, NEW_CD), ("cd_end", OLD_CD_END, NEW_CD_END)]

if "_add_binding(\"repl.submit\"" in src:
    print("SKIP ai_interactive: 已改造")
else:
    for name, old, new in pairs:
        n = src.count(old)
        if n != 1:
            print(f"FAIL {name}: 命中 {n} 次")
            sys.exit(1)
        src = src.replace(old, new, 1)
        print(f"OK   {name}")
    tmp = P + ".tmp"
    io.open(tmp, "w", encoding="utf-8").write(src)
    os.replace(tmp, P)
    print("已写回")
