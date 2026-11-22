# -*- coding: utf-8 -*-
"""补丁 5：去掉多余的 _AI_TURN_ACTIVE，改在调用点 finally 里 turn_end()。"""
import io
import sys


def patch(path, old, new, cnt=1):
    s = io.open(path, encoding="utf-8").read()
    c = s.count(old)
    if c != cnt:
        print("FAIL %s count=%d (expect %d) %r" % (path, c, cnt, old[:70]))
        sys.exit(1)
    io.open(path, "w", encoding="utf-8").write(s.replace(old, new, cnt))
    print("OK %s" % path)


# ── 1. 撤掉 _AI_TURN_ACTIVE（senses.turn_begin/end 是唯一真源）──
patch("bin/ai_cmd.py",
      '''_PLAN_MODE_ACTIVE = False  # 全局 plan 模式标记

# 感官系统：AI 回合是否进行中（后台感知线程据此决定「现在别打扰」）
_AI_TURN_ACTIVE = threading.Event()''',
      '''_PLAN_MODE_ACTIVE = False  # 全局 plan 模式标记''')

patch("bin/ai_cmd.py",
      '''    # ── 感官：标记 AI 回合开始（回合内文件变化只更新视觉，不主动唤醒）──
    _AI_TURN_ACTIVE.set()
    try:''',
      '''    # ── 感官：标记 AI 回合开始（回合内文件变化只更新视觉，不主动唤醒）──
    try:''')

# ── 2. Onyx.py 一次性路径：finally 收尾 ──
patch("Onyx.py",
      '''            from bin.ai_cmd import handle_ai as _handle_ai
            _handle_ai(''',
      '''            from bin.ai_cmd import handle_ai as _handle_ai
            from bin.ai_lib import senses as _senses_turn
            try:
                _handle_ai(''')

patch("Onyx.py",
      '''                security_log=security_log,
            )
        else:''',
      '''                security_log=security_log,
                )
            finally:
                _senses_turn.turn_end()
        else:''')

# ── 3. REPL 路径：finally 收尾 ──
patch("bin/ai_interactive.py",
      '''        handle_ai(
            cmd_parts=cmd_parts,
            request_id=call_request_id,''',
      '''        from bin.ai_lib import senses as _senses_turn
        try:
            handle_ai(
            cmd_parts=cmd_parts,
            request_id=call_request_id,''')

patch("bin/ai_interactive.py",
      '''            **{k: v for k, v in kwargs.items() if k not in ("cmd_parts", "request_id", "onyx_module", "user_home_dir", "global_config", "user_info", "user_mode", "parse_and_execute", "plus_think")}
        )''',
      '''            **{k: v for k, v in kwargs.items() if k not in ("cmd_parts", "request_id", "onyx_module", "user_home_dir", "global_config", "user_info", "user_mode", "parse_and_execute", "plus_think")}
            )
        finally:
            _senses_turn.turn_end()''')
