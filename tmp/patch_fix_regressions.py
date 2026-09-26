# -*- coding: utf-8 -*-
"""回归修复（合并版）：外观回退 + 键位作用域 + 虚影/自动弹出。

原则：**换内核，不换外观**。
"""
import io
import sys


def rep(path, old, new, tag):
    s = io.open(path, encoding="utf-8").read()
    n = s.count(old)
    if n != 1:
        print(f"❌ {tag}: 命中 {n} 次")
        sys.exit(1)
    io.open(path, "w", encoding="utf-8").write(s.replace(old, new))
    print(f"✅ {tag}")


E = "lib/terminal/repl/editor.py"
H = "lib/terminal/repl/history.py"
M = "lib/terminal/repl/multiline.py"
I = "lib/terminal/repl/__init__.py"
C = "lib/terminal/repl/complete.py"
L = "lib/terminal/input_lib.py"

# ══════════ ① editor.py：加 main_buffer_filter + 各绑定加 filter ══════════
rep(E, '''def build_key_bindings(extra=None):
    """构造键位。`extra` 可传一个已建好的 KeyBindings 用于叠加（如多行、历史）。

    只**新增/修正**必要绑定；其余交给 prompt_toolkit 默认（emacs 模式），
    这样 Ctrl+A/E/B/F/T/_ 等一批标准行为保持原生实现，避免重复实现引入偏差。
    """
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys

    kb = KeyBindings()''',
    '''def main_buffer_filter():
    """只作用于「主输入缓冲区」的 filter。

    ⚠️ 必须加：不带 filter 的绑定会**抢走搜索缓冲区（Ctrl+R）的按键** ——
    Enter 被多行绑定截走导致搜索无法接受、↑/↓ 被前缀历史截走导致无法上下选。
    """
    try:
        from prompt_toolkit.enums import DEFAULT_BUFFER
        from prompt_toolkit.filters import has_focus
        return has_focus(DEFAULT_BUFFER)
    except Exception:
        return None


def build_key_bindings(extra=None):
    """构造键位。`extra` 可传一个已建好的 KeyBindings 用于叠加（如多行、历史）。

    只**新增/修正**必要绑定；其余交给 prompt_toolkit 默认（emacs 模式），
    这样 Ctrl+A/E/B/F/T/_ 等一批标准行为保持原生实现，避免重复实现引入偏差。
    """
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys

    kb = KeyBindings()
    _MAIN = main_buffer_filter()''', "editor main_buffer_filter")

for _k in ['@kb.add("c-k")', '@kb.add("c-u")', '@kb.add("c-w")',
           '@kb.add("escape", "d")', '@kb.add("c-y")', '@kb.add("escape", "y")',
           '@kb.add("escape", ".")', '@kb.add("escape", "enter")',
           '@kb.add("f1")', '@kb.add("c-x", "c-r")']:
    rep(E, _k, _k[:-1] + ", filter=_MAIN)", f"filter {_k}")

# ══════════ ② history.py：↑/↓ 加 filter + 补全菜单优先 ══════════
rep(H, '''def install_history_bindings(kb, history) -> PrefixNav:
    """把 ↑/↓ 换成非破坏性前缀导航（追加到现有 KeyBindings）。"""
    nav = PrefixNav(history)

    @kb.add("up")
    def _up(event):
        nav.up(event)

    @kb.add("down")
    def _down(event):
        nav.down(event)

    return nav''',
    '''def install_history_bindings(kb, history, filter=None) -> PrefixNav:
    """把 ↑/↓ 换成非破坏性前缀导航（追加到现有 KeyBindings）。

    - 补全菜单打开时，↑/↓ **优先导航菜单**（否则自动弹出来的列表没法选）；
    - 只作用于主输入缓冲区（否则会抢走 Ctrl+R 搜索缓冲区的 ↑/↓）。
    """
    nav = PrefixNav(history)
    kw = {"filter": filter} if filter is not None else {}

    @kb.add("up", **kw)
    def _up(event):
        b = event.current_buffer
        if b.complete_state:
            b.complete_previous()
            return
        nav.up(event)

    @kb.add("down", **kw)
    def _down(event):
        b = event.current_buffer
        if b.complete_state:
            b.complete_next()
            return
        nav.down(event)

    return nav''', "history ↑/↓")

# ══════════ ③ multiline.py：Enter 加 filter（仅单缓冲模式用）══════════
rep(M, '''def install_multiline_bindings(kb, shell: str = "bash"):
    """Enter：完整则提交，不完整则插入换行 + 自动缩进。"""

    @kb.add("enter")''',
    '''def install_multiline_bindings(kb, shell: str = "bash", filter=None):
    """Enter：完整则提交，不完整则插入换行 + 自动缩进（**单缓冲模式**）。

    必须限定在主输入缓冲区：否则 Ctrl+R 搜索时按 Enter 会被这里截走。
    """
    kw = {"filter": filter} if filter is not None else {}

    @kb.add("enter", **kw)''', "multiline enter filter")

# ══════════ ④ __init__.py：外观回退 + filter 传递 ══════════
rep(I, '''    kb = _editor.build_key_bindings()
    if history is not None:
        _history.install_history_bindings(kb, history)
    _multiline.install_multiline_bindings(kb, shell=shell)''',
    '''    _MAIN = _editor.main_buffer_filter()
    kb = _editor.build_key_bindings()
    if history is not None:
        _history.install_history_bindings(kb, history, filter=_MAIN)
    if multiline:
        _multiline.install_multiline_bindings(kb, shell=shell, filter=_MAIN)''',
    "__init__ 键位装配")

rep(I, '''    kwargs = dict(_editor.SESSION_FLAGS)
    kwargs.update(_multiline.MULTILINE_FLAGS)
    kwargs["style"] = _theme.build_style()
    kwargs["bottom_toolbar"] = _hints.make_toolbar()
    kwargs["key_bindings"] = kb
    kwargs["completer"] = completer
    if history is not None:
        kwargs["history"] = history''',
    '''    kwargs = dict(_editor.SESSION_FLAGS)
    if multiline:
        kwargs.update(_multiline.MULTILINE_FLAGS)
    # 外观：优先用调用方**原有的**样式（补全菜单配色），没有才回退到新主题
    kwargs["style"] = style if style is not None else _theme.build_style()
    if toolbar:
        kwargs["bottom_toolbar"] = _hints.make_toolbar()
    if auto_suggest is not None:
        kwargs["auto_suggest"] = auto_suggest      # 虚影 / 幽灵补全
    kwargs["key_bindings"] = kb
    kwargs["completer"] = completer
    if history is not None:
        kwargs["history"] = history''', "__init__ 外观参数")

# ══════════ ⑤ complete.py：命中高亮默认关 ══════════
rep(C, '''    def __init__(self, commands=None, dir_cache=None, freq=None, recent=None,
                 virtual_root: str = "", show_hidden: bool = False):''',
    '''    def __init__(self, commands=None, dir_cache=None, freq=None, recent=None,
                 virtual_root: str = "", show_hidden: bool = False,
                 highlight_matches: bool = False):''', "OnyxCompleter 签名")

rep(C, '''        self.virtual_root = virtual_root or ""
        self.show_hidden = show_hidden''',
    '''        self.virtual_root = virtual_root or ""
        self.show_hidden = show_hidden
        # 命中字符高亮需要样式里定义 `tok.match`；**原有补全菜单样式没有这条**，
        # 开着会让命中字符掉色 → 默认关（观感与改动前一致）。
        self.highlight_matches = highlight_matches''', "highlight_matches")

rep(C, '''                yield Completion(
                    name, start_position=-len(word),
                    display=_highlight(name, pos),
                    display_meta=("cmd" if self.freq.get(name) else ""),
                )''',
    '''                yield Completion(
                    name, start_position=-len(word),
                    display=_highlight(name, pos) if self.highlight_matches else name,
                    display_meta=("cmd" if self.freq.get(name) else ""),
                )''', "命令 display")

rep(C, '''            yield Completion(
                text,
                start_position=-len(base),
                display=_highlight(shown_dir + name + ("/" if is_dir else ""),
                                   _match_positions(base, name)),
                display_meta=meta,
            )''',
    '''            shown = shown_dir + name + ("/" if is_dir else "")
            yield Completion(
                text,
                start_position=-len(base),
                display=(_highlight(shown, _match_positions(base, name))
                         if self.highlight_matches else shown),
                display_meta=meta,
            )''', "路径 display")

# ══════════ ⑥ input_lib：传入原有样式与虚影 ══════════
rep(L, '''def _build_v2_session(completion_items, sys_type, virtual_root, user_home_dir):
    """按签名复用新输入层的 PromptSession（配置/对象只在变化时重建）。"""''',
    '''def _build_v2_session(completion_items, sys_type, virtual_root, user_home_dir,
                      style=None, auto_suggest=None):
    """按签名复用新输入层的 PromptSession（配置/对象只在变化时重建）。

    传入 `style` / `auto_suggest` 是为了**保持原有观感**：补全菜单配色与虚影补全
    沿用调用方原来那套，新输入层只替换内部实现。
    """''', "_build_v2_session 签名")

rep(L, '''    return rs.get(key, lambda: build_session(
        commands=cmds,
        history=_get_v2_history(),
        virtual_root=virtual_root or "",
    ))''',
    '''    return rs.get(key, lambda: build_session(
        commands=cmds,
        history=_get_v2_history(),
        virtual_root=virtual_root or "",
        style=style,
        auto_suggest=auto_suggest,
        toolbar=False,      # 原来没有底部工具条
        multiline=False,    # 原来的多行是逐行流程
    ))''', "_build_v2_session 转发")

rep(L, '''            session = _build_v2_session(completion_items, sys_type, virtual_root, user_home_dir)''',
    '''            session = _build_v2_session(completion_items, sys_type, virtual_root,
                                        user_home_dir,
                                        style=comp_style, auto_suggest=auto_suggest)''',
    "调用点")

print("done")
