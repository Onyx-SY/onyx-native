#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""REPL 刷新优化：复用 PromptSession（避免每次重建会话）。"""
p = "lib/terminal/input_lib.py"
s = open(p, encoding="utf-8").read()

# 1) 导入 PromptSession
old = "from prompt_toolkit import prompt\n"
new = "from prompt_toolkit import prompt, PromptSession\n"
assert s.count(old) == 1, ("import anchor", s.count(old))
s = s.replace(old, new)

# 2) 模块级会话缓存
old = "from prompt_toolkit.validation import Validator, ValidationError\n"
new = ("from prompt_toolkit.validation import Validator, ValidationError\n\n"
       "# ── PromptSession 复用缓存：prompt() 快捷函数每次调用都会重建会话（约 20~45ms），\n"
       "#    按输入签名缓存后，提示符刷新不再付出会话构建开销 ──\n"
       "_SESSION_CACHE = {\"key\": None, \"session\": None}\n")
assert s.count(old) == 1, ("validation anchor", s.count(old))
s = s.replace(old, new)

# 3) 主 prompt 调用改为复用会话
old = '''        prompt_text = prompt_func()
        user_input = prompt(
            prompt_text,
            completer=completer,
            lexer=lexer,
            complete_while_typing=completion_typing_filter,
            style=comp_style,
            key_bindings=kb,
            # mouse_support 已移除，避免鼠标接管终端滚动
            complete_in_thread=True,
            reserve_space_for_menu=6,
            auto_suggest=auto_suggest,
        )'''
new = '''        prompt_text = prompt_func()
        # 复用 PromptSession（prompt() 快捷函数每次都会重建会话，约 20~45ms/次）
        try:
            _vc = _VALID_COMMANDS
            _vhash = hash(frozenset(_vc.keys() if isinstance(_vc, dict) else _vc))
        except Exception:
            _vhash = -1
        _cache_key = (
            virtual_root, sys_type, get_terminal_type(), _vhash,
            repr(sorted((_ptk_config.get("colors") or {}).items())),
        )
        if _SESSION_CACHE.get("key") == _cache_key and _SESSION_CACHE.get("session") is not None:
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
        user_input = session.prompt(prompt_text)'''
assert s.count(old) == 1, ("prompt call anchor", s.count(old))
s = s.replace(old, new)

open(p, "w", encoding="utf-8").write(s)
print("patched lib/terminal/input_lib.py")
