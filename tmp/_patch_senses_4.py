# -*- coding: utf-8 -*-
"""补丁 4：eyes 工具注册 / 视觉注入 / 自编辑打点 / 忙碌标记。"""
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


# ══════════════ 1. senses.py：忙碌标记 ══════════════
patch("bin/ai_lib/senses.py",
      '''def status_text() -> str:''',
      '''_TURN_BUSY = threading.Event()
_TURN_BUSY_SINCE = [0.0]


def turn_begin() -> None:
    """主线程 AI 回合开始（后台感知据此判断「现在别打扰」）。"""
    _TURN_BUSY_SINCE[0] = time.time()
    _TURN_BUSY.set()


def turn_end() -> None:
    _TURN_BUSY.clear()


def is_busy() -> bool:
    """AI 回合是否进行中；超过 30 分钟视为异常残留，自动失效。"""
    if not _TURN_BUSY.is_set():
        return False
    if time.time() - _TURN_BUSY_SINCE[0] > 1800:
        _TURN_BUSY.clear()
        return False
    return True


def status_text() -> str:''')

# ══════════════ 2. native_tools.py：eyes schema ══════════════
patch("bin/ai_lib/native_tools.py",
      '''        _make_tool(
            "glob_search",''',
      '''        _make_tool(
            "eyes",
            "AI 的眼睛（视觉感官）。在多个文件的多个片段（或片段内的关键词附近）设置「注意力」，"
            "后台感官线程实时监视；被外部改动且静默 N 秒后，在 AI 空闲时主动唤醒你并附上改前/改后。"
            "视觉内容随每次请求附在记忆底部，但不写入会话记忆。"
            "action=add_file|add_tool|list|update|remove|clear|read_now|status。",
            {
                "action": {"type": "string",
                           "enum": ["add_file", "add_tool", "list", "update", "remove",
                                    "clear", "read_now", "status"],
                           "description": "操作类型"},
                "path": {"type": "string", "description": "文件路径（与 read_file 相同的虚拟路径）"},
                "start": {"type": "integer", "description": "起始行（1 起）"},
                "end": {"type": "integer", "description": "结束行；0 表示到文件末尾"},
                "keyword": {"type": "string", "description": "只盯该关键词附近的片段（更细粒度）"},
                "context": {"type": "integer", "description": "关键词上下文大小（配合 unit）"},
                "unit": {"type": "string", "enum": ["lines", "bytes"],
                         "description": "上下文单位：lines=±N 行，bytes=±N 字节"},
                "trigger": {"type": "string",
                            "enum": ["on_change", "on_idle", "interval", "on_tool",
                                     "on_keyword_change", "manual"],
                            "description": "何时提醒：on_change=改动+静默后；interval=定时；manual=只看不提醒"},
                "debounce_sec": {"type": "number", "description": "改动后静默多少秒才算改完（默认 3）"},
                "check_interval_sec": {"type": "number",
                                       "description": "文件检测颗粒度：每 N 秒采样一次（0=默认 1s，可设 20/600）"},
                "silent_sec": {"type": "number",
                               "description": "长时间无变化时，每隔这么久主动提醒一次（0=不提醒）"},
                "interval_sec": {"type": "number", "description": "定时提醒/定时执行工具的间隔秒数"},
                "exec_tool": {"type": "boolean",
                              "description": "interval 时是否由感官线程自动执行该工具（仅限只读白名单）"},
                "tool": {"type": "string", "description": "工具名（add_tool 时必填）"},
                "match": {"type": "string", "description": "工具返回内容里要匹配的正则/关键词"},
                "notify": {"type": "boolean", "description": "定时任务是否也主动提醒（默认 true）"},
                "id": {"type": "integer", "description": "注意力编号（update/remove/read_now 用）"},
                "note": {"type": "string", "description": "备注（会显示在视觉文档里）"},
            },
            ["action"],
            PERM_READONLY,
        ),
        _make_tool(
            "glob_search",''')

# ══════════════ 3. tool_executors.py：eyes 执行器 + 自编辑打点 ══════════════
patch("bin/ai_lib/tool_executors.py",
      '''def _exec_write_file(file_path: str, content: str) -> str:''',
      '''def _note_self_edit(path: str) -> None:
    """告诉感官系统：这次改动是 AI 自己做的（视觉照改，但不主动提醒）。"""
    try:
        from . import senses
        senses.note_self_edit(path)
    except Exception:
        pass


def _exec_eyes(params: dict) -> str:
    """eyes 工具：设置/查看 AI 的视觉注意力。"""
    try:
        from . import senses
        return senses.handle_eyes_action(**(params or {}))
    except Exception as e:
        return "❌ eyes failed: %r" % (e,)


def _exec_write_file(file_path: str, content: str) -> str:''')

patch("bin/ai_lib/tool_executors.py",
      '''        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        total_lines = content.count("\\n") + (1 if content else 0)''',
      '''        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        _note_self_edit(file_path)
        total_lines = content.count("\\n") + (1 if content else 0)''')

patch("bin/ai_lib/tool_executors.py",
      '''        ok, msg = apply_edit(file_path, old_string, new_string, replace_all=replace_all)
        if ok:''',
      '''        ok, msg = apply_edit(file_path, old_string, new_string, replace_all=replace_all)
        if ok:
            _note_self_edit(file_path)''')

patch("bin/ai_lib/tool_executors.py",
      '''        with open(path, "w", encoding="utf-8") as f:
            f.write(original)
        _LAST_EDIT = {}''',
      '''        with open(path, "w", encoding="utf-8") as f:
            f.write(original)
        _note_self_edit(path)
        _LAST_EDIT = {}''')

# ══════════════ 4. mcp_exec.py：handler 映射 ══════════════
patch("bin/ai_lib/mcp_exec.py",
      '''    _exec_undo_last_edit, _exec_validate_edit, _exec_write_file,
)''',
      '''    _exec_undo_last_edit, _exec_validate_edit, _exec_write_file, _exec_eyes,
)''')

patch("bin/ai_lib/mcp_exec.py",
      '''        "read_file":    lambda p: _exec_read_file(_path_arg(p), p.get("range", None), p.get("head", None), p.get("tail", None)),''',
      '''        "read_file":    lambda p: _exec_read_file(_path_arg(p), p.get("range", None), p.get("head", None), p.get("tail", None)),
        # ── 感官（视觉注意力）：参数不经过路径归一，path 由 senses 自己解析 ──
        "eyes":         lambda p: _exec_eyes(p),''')

# ══════════════ 5. ai_cmd.py：忙碌标记 + 视觉注入 + 工具事件 + 命令窗口 ══════════════
patch("bin/ai_cmd.py",
      '''_PLAN_MODE_ACTIVE = False  # 全局 plan 模式标记''',
      '''_PLAN_MODE_ACTIVE = False  # 全局 plan 模式标记

# 感官系统：AI 回合是否进行中（后台感知线程据此决定「现在别打扰」）
_AI_TURN_ACTIVE = threading.Event()''')

patch("bin/ai_cmd.py",
      '''    from io import StringIO
    import sys as sys_module''',
      '''    # ── 感官：标记 AI 回合开始（回合内文件变化只更新视觉，不主动唤醒）──
    _AI_TURN_ACTIVE.set()
    try:
        from .ai_lib import senses as _senses_turn
        _senses_turn.turn_begin()
    except Exception:
        pass

    from io import StringIO
    import sys as sys_module''')

patch("bin/ai_cmd.py",
      '''        _dynamic_env = "\\n\\n".join(_dynamic_env_parts) if _dynamic_env_parts else ""''',
      '''        # ── 感官（视觉）：实时视觉块，拼在记忆/动态段的**最底部** ──
        # 不写入 messages[0] 静态前缀，也不写入会话记忆；开关关闭时为空串（零成本）。
        try:
            from .ai_lib import senses as _senses_env
            _vision_block = _senses_env.get_vision_block()
            if _vision_block:
                _dynamic_env_parts.append(_vision_block.rstrip())
        except Exception:
            pass
        _dynamic_env = "\\n\\n".join(_dynamic_env_parts) if _dynamic_env_parts else ""''')

patch("bin/ai_cmd.py",
      '''                    # ── Plan 模式：用户通过 choose_ask 选择切换到自由模式 → 记录待系统处理 ──''',
      '''                    # ── 感官：工具注意力（有 tool 注意力时才记录，否则零开销）──
                    try:
                        from .ai_lib import senses as _senses_tool
                        _senses_tool.emit_tool_event(tool_name, str(params)[:800], output)
                    except Exception:
                        pass

                    # ── Plan 模式：用户通过 choose_ask 选择切换到自由模式 → 记录待系统处理 ──''')

patch("bin/ai_cmd.py",
      '''    executor = get_main_command_executor()
    if executor is None:
        return "⛔ RunCommand 不可用：主会话未初始化命令执行器（安全管线未注入）"
    try:
        return executor(command)''',
      '''    executor = get_main_command_executor()
    if executor is None:
        return "⛔ RunCommand 不可用：主会话未初始化命令执行器（安全管线未注入）"
    # 感官：命令执行窗口内的文件变化视为「疑似 AI 自己改的」（视觉照改、不主动提醒）
    try:
        from .ai_lib import senses as _senses_cmd
        _senses_cmd.begin_command_window()
    except Exception:
        pass
    try:
        return executor(command)''')

patch("bin/ai_cmd.py",
      '''    try:
        return executor(command)
    except Exception as e:
        return f"命令执行失败: {e}"''',
      '''    try:
        return executor(command)
    except Exception as e:
        return f"命令执行失败: {e}"
    finally:
        try:
            from .ai_lib import senses as _senses_cmd2
            _senses_cmd2.end_command_window()
        except Exception:
            pass''')
