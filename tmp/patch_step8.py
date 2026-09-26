# -*- coding: utf-8 -*-
"""step-8 接线：让 input_lib.universal_input 走新的 lib/terminal/repl 输入层。

保留 `universal_input()` 的调用契约（返回 str / 空串语义 / Ctrl+C·D 行为），
只在内部把「会话构建 + 多行 + 历史 + 键位 + 补全」换成新实现。
ONYX_REPL_V2=0 可整体回退。
"""
import io
import sys

P = "lib/terminal/input_lib.py"
s = io.open(P, encoding="utf-8").read()


def rep(old, new, tag):
    global s
    n = s.count(old)
    if n != 1:
        print(f"❌ {tag}: 命中 {n} 次")
        sys.exit(1)
    s = s.replace(old, new)
    print(f"✅ {tag}")


# ── W1：开关 + 会话构建助手 ─────────────────────────────────────
rep('''def __getattr__(name):
    """兼容外部 `input_lib.HAS_PYGMENTS` 写法（动态转发到 mul_line）。"""
    if name == "HAS_PYGMENTS":
        return _has_pygments()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")''',
    '''def __getattr__(name):
    """兼容外部 `input_lib.HAS_PYGMENTS` 写法（动态转发到 mul_line）。"""
    if name == "HAS_PYGMENTS":
        return _has_pygments()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ══════════════════════════════════════════════════════════════
# 新输入层开关（lib/terminal/repl）
# ══════════════════════════════════════════════════════════════
# 默认启用重写后的输入层：会话级复用、readline 全量键位 + kill-ring、
# 非破坏性前缀历史 + Ctrl+R、目录级缓存补全、单缓冲多行、工具条 + F1 帮助。
# 设 ONYX_REPL_V2=0 可整体回退到旧实现（逐行多行循环 + 每次回车重建会话）。
_REPL_V2 = os.environ.get("ONYX_REPL_V2", "1").strip().lower() not in (
    "0", "false", "no", "off")

_V2_HISTORY = None


def _get_v2_history():
    """新输入层的历史后端（进程内单例）。"""
    global _V2_HISTORY
    if _V2_HISTORY is None:
        from lib.terminal.repl.history import OnyxHistory
        _V2_HISTORY = OnyxHistory(_get_history_file_path())
    return _V2_HISTORY


def _build_v2_session(completion_items, sys_type, virtual_root, user_home_dir):
    """按签名复用新输入层的 PromptSession（配置/对象只在变化时重建）。"""
    from lib.terminal.repl import build_session, get_repl_session, session_key
    from lib.terminal.repl import hints as _hints

    cmds = tuple(sorted(completion_items or ()))
    key = session_key(
        commands_hash=hash(frozenset(cmds)),
        sys_type=sys_type or "",
        terminal_type=_TERMINAL_TYPE or "",
        virtual_root=virtual_root or "",
        user_home_dir=user_home_dir or "",
        lang=_CURRENT_LANG or "",
    )
    try:
        _hints.set_state(mode="SHELL")
    except Exception:
        pass
    rs = get_repl_session("main")
    return rs.get(key, lambda: build_session(
        commands=cmds,
        history=_get_v2_history(),
        virtual_root=virtual_root or "",
    ))''',
    "W1 开关与构建助手")

# ── W2：新输入层不再初始化旧的 _HISTORY_BUFFER ──────────────────
rep('''    if not _HISTORY_INITIALIZED:
        init_history_navigation()''',
    '''    if not _HISTORY_INITIALIZED and not _REPL_V2:
        # 新输入层用 OnyxHistory + PrefixNav，旧的 _HISTORY_BUFFER 不再需要
        init_history_navigation()''',
    "W2 历史初始化守卫")

# ── W3：add_to_history 在 v2 下不再写盘（避免写坏新格式）────────
rep('''def add_to_history(cmd: str) -> bool:
    """添加命令到历史记录"""
    global _HISTORY_BUFFER''',
    '''def add_to_history(cmd: str) -> bool:
    """添加命令到历史记录"""
    if _REPL_V2:
        # 新输入层：历史由 OnyxHistory 负责（prompt_toolkit 在接受输入时自动落盘）。
        # 这里再写一次会把新格式文件写坏（旧格式是「一行一条 + \\n 猜测」）。
        return False
    global _HISTORY_BUFFER''',
    "W3 add_to_history 守卫")

# ── W4：会话构建 + prompt 调用 ─────────────────────────────────
rep('''        if _SESSION_CACHE.get("key") == _cache_key and _SESSION_CACHE.get("session") is not None:
            session = _SESSION_CACHE["session"]
        else:
            session = PromptSession(
                completer=completer,
                lexer=lexer,
                complete_while_typing=completion_typing_filter,
                style=comp_style,
                key_bindings=kb,
                # mouse_support 已移除，避免鼠标接管终端滚动
                complete_in_thread=True,
                reserve_space_for_menu=6,
                auto_suggest=auto_suggest,
            )
            _SESSION_CACHE["key"] = _cache_key
            _SESSION_CACHE["session"] = session
        user_input = session.prompt(prompt_text)''',
    '''        if _REPL_V2:
            # 新输入层：会话（样式/键位/历史/补全/多行/工具条）整体缓存，
            # 签名不变时不再重建，也不再每 prompt 读盘。
            session = _build_v2_session(completion_items, sys_type, virtual_root, user_home_dir)
        elif _SESSION_CACHE.get("key") == _cache_key and _SESSION_CACHE.get("session") is not None:
            session = _SESSION_CACHE["session"]
        else:
            session = PromptSession(
                completer=completer,
                lexer=lexer,
                complete_while_typing=completion_typing_filter,
                style=comp_style,
                key_bindings=kb,
                # mouse_support 已移除，避免鼠标接管终端滚动
                complete_in_thread=True,
                reserve_space_for_menu=6,
                auto_suggest=auto_suggest,
            )
            _SESSION_CACHE["key"] = _cache_key
            _SESSION_CACHE["session"] = session

        if _REPL_V2:
            # 单缓冲多行：Enter 由 repl.multiline 的绑定决定「提交 or 换行」，
            # 续行提示符显示「还在等什么」（fi / done / EOF / " …）。
            from lib.terminal.repl.multiline import continuation_prompt, is_complete as _v2_complete

            def _v2_cont(width, line_number, is_soft_wrap):
                try:
                    return continuation_prompt(_v2_complete(session.default_buffer.text))
                except Exception:
                    return "… "

            user_input = session.prompt(prompt_text, prompt_continuation=_v2_cont)
        else:
            user_input = session.prompt(prompt_text)''',
    "W4 会话构建与 prompt")

# ── W5：新输入层不再走旧的逐行多行循环 ──────────────────────────
rep('''        if user_input_stripped:
            multiline_result = _process_multiline_input(''',
    '''        if user_input_stripped and not _REPL_V2:
            # 新输入层：多行已在同一个缓冲区里完成（Enter 绑定 + 完整性判定），
            # 再走旧的逐行循环会把整段命令拆散。
            multiline_result = _process_multiline_input(''',
    "W5 旧多行循环守卫")

io.open(P, "w", encoding="utf-8").write(s)
print("written")
