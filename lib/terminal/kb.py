# lib/terminal/kb.py
"""
键盘绑定模块
提供上下键历史导航、前缀历史导航（Alt+上下）、补全菜单选择等绑定
支持从 ptk.json 配置加载键位
修复：右键逐项补全（Tab 键下一项，Shift+Tab 上一项）
新增：补全菜单翻页键绑定（PageUp/PageDown）
新增：use_dropdown_menu 开关，关闭后 Tab 直接内联接受首个补全
修复：default_keys 补齐（此前 completion_menu_up/down、completion_trigger、
      completion_lock、multiline_editor 等键从未注册，导致 Alt+Enter、
      ESC+Space、Ctrl+Space 全部无效）

修复（坏配置自愈专项 · P1）：
- 新增 _BUILTIN_DEFAULTS：内置默认键位快照，独立于用户 ptk.json
- _add() 统一走「用户配置 → 内置默认 → no-op」三级回退，
  此前 8 处直接 @kb.add(default_keys[...]) 的绑定在用户配错键名时
  会抛 ValueError，被 universal_input 顶层 except 吞成返回空串，
  现象是「输入框再也不接受任何输入」，用户完全无法自救
- 现在即使 ptk.json 写了 "history_up": "!!!bad"，也能照常使用
  内置的 up 键，输入层保持可用
"""

from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.filters import Condition
from prompt_toolkit.application import get_app
import os
from typing import Dict, Any

# 全局补全锁定状态：ESC+Space 切换
# True = 补全被锁定（输入时不自动弹出补全菜单）
# False = 正常模式（输入时自动触发补全）
_completion_locked = False


# ═══════════════════════════════════════════════════════════
# 内置默认键位快照（不随用户 ptk.json 变化）
# ═══════════════════════════════════════════════════════════
# _add() 在用户配置无效时会回退到这里的值。
# 只要这里的键名是 prompt_toolkit 认识的，输入层就不会因为用户配错而瘫痪。
_BUILTIN_DEFAULTS: Dict[str, str] = {
    "history_up": "up",
    "history_down": "down",
    "prefix_history_up": "escape, up",
    "prefix_history_down": "escape, down",
    "completion_next": "tab",
    "completion_prev": "s-tab",
    "clear_screen": "c-l",
    "completion_page_up": "pageup",
    "completion_page_down": "pagedown",
    "completion_menu_up": "c-up",
    "completion_menu_down": "c-down",
    "completion_trigger": "c-space",
    "completion_alt_next": "c-n",
    "completion_alt_prev": "c-p",
    "completion_lock": "escape, space",
    "multiline_editor": "escape, enter",
}


def is_completion_locked() -> bool:
    """返回当前补全是否被全局锁定"""
    return _completion_locked


def _split_keys(raw: str):
    """把 'escape, up' 切成 ['escape', 'up']；空串返回 []。"""
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def create_key_bindings(
    sys_type: str = "",
    terminal_type: str = "bash",
    ptk_config: Dict[str, Any] = None,
    use_dropdown_menu: bool = True,
) -> KeyBindings:
    """
    创建并返回 prompt_toolkit 的 KeyBindings 对象。

    Args:
        sys_type: 系统类型 (Windows/Linux/Mac)
        terminal_type: 终端类型 (bash/cmd/powershell/zsh/fish)
        ptk_config: 来自 ptk.json 的配置字典
        use_dropdown_menu: True 时 Tab 在补全菜单里上下选择；
                           False 时 Tab 直接内联接受第一个补全（无下拉菜单）
    """
    from . import input_lib as input_lib_module

    kb = KeyBindings()

    # ── default_keys：在内置默认基础上，用用户配置覆盖（仅覆盖非空值）──
    default_keys = dict(_BUILTIN_DEFAULTS)
    if ptk_config and "key_bindings" in ptk_config:
        for key, value in ptk_config["key_bindings"].items():
            if value:
                default_keys[key] = value

    def _add(action_key: str):
        """按配置绑定 handler；配置无效时回退内置默认；仍无效则跳过。

        三级回退：
          1. 用户 ptk.json 里的键名（default_keys）
          2. 内置默认键名（_BUILTIN_DEFAULTS）
          3. 无绑定（lambda f: f）—— 至少不让 create_key_bindings 抛异常

        返回一个可当装饰器使用的函数，保证所有绑定路径统一走这里。
        """
        raw = default_keys.get(action_key) or ""
        parts = _split_keys(raw)
        if parts:
            try:
                return kb.add(*parts)
            except Exception:
                pass

        # 回退内置默认
        fallback_parts = _split_keys(_BUILTIN_DEFAULTS.get(action_key, ""))
        if fallback_parts:
            try:
                return kb.add(*fallback_parts)
            except Exception:
                pass

        # 最后兜底：什么都不绑，但也不抛异常
        return lambda f: f

    # ── 普通上下键：永远遍历全部历史 ──
    @_add("history_up")
    def _(event):
        buffer = event.app.current_buffer
        new_text, new_pos = input_lib_module.handle_up_arrow_normal(buffer.text)
        if new_text != buffer.text:
            buffer.text = new_text
            buffer.cursor_position = new_pos

    @_add("history_down")
    def _(event):
        buffer = event.app.current_buffer
        new_text, new_pos = input_lib_module.handle_down_arrow_normal(buffer.text)
        if new_text != buffer.text:
            buffer.text = new_text
            buffer.cursor_position = new_pos

    # ── Alt+上下键 / Shift+上下键：前缀历史导航 ──
    # 这两个是硬编码的安全兜底键位，无论配置怎样都必须可用
    @kb.add('escape', 'up')
    @kb.add('s-up')
    def prefix_up(event):
        buffer = event.app.current_buffer
        if buffer.complete_state:
            buffer.cancel_completion()
        new_text, new_pos = input_lib_module.handle_up_arrow_with_prefix(buffer.text)
        if new_text != buffer.text:
            buffer.text = new_text
            buffer.cursor_position = new_pos

    @kb.add('escape', 'down')
    @kb.add('s-down')
    def prefix_down(event):
        buffer = event.app.current_buffer
        if buffer.complete_state:
            buffer.cancel_completion()
        new_text, new_pos = input_lib_module.handle_down_arrow_with_prefix(buffer.text)
        if new_text != buffer.text:
            buffer.text = new_text
            buffer.cursor_position = new_pos

    # 用户自定义了前缀导航键位时，额外绑定一份（保留用户选择）
    prefix_up_keys = default_keys.get("prefix_history_up", "")
    if prefix_up_keys and prefix_up_keys not in ("escape, up", "s-up"):
        _custom_prefix_up_parts = _split_keys(prefix_up_keys)
        if _custom_prefix_up_parts:
            try:
                @kb.add(*_custom_prefix_up_parts)
                def custom_prefix_up(event):
                    buffer = event.app.current_buffer
                    if buffer.complete_state:
                        buffer.cancel_completion()
                    new_text, new_pos = input_lib_module.handle_up_arrow_with_prefix(buffer.text)
                    if new_text != buffer.text:
                        buffer.text = new_text
                        buffer.cursor_position = new_pos
            except Exception:
                pass

    prefix_down_keys = default_keys.get("prefix_history_down", "")
    if prefix_down_keys and prefix_down_keys not in ("escape, down", "s-down"):
        _custom_prefix_down_parts = _split_keys(prefix_down_keys)
        if _custom_prefix_down_parts:
            try:
                @kb.add(*_custom_prefix_down_parts)
                def custom_prefix_down(event):
                    buffer = event.app.current_buffer
                    if buffer.complete_state:
                        buffer.cancel_completion()
                    new_text, new_pos = input_lib_module.handle_down_arrow_with_prefix(buffer.text)
                    if new_text != buffer.text:
                        buffer.text = new_text
                        buffer.cursor_position = new_pos
            except Exception:
                pass

    # ── Ctrl+上下键：补全菜单选择 ──
    @_add("completion_menu_up")
    def _(event):
        buffer = event.app.current_buffer
        if buffer.complete_state:
            buffer.complete_previous()
        else:
            buffer.start_completion(select_first=False)

    @_add("completion_menu_down")
    def _(event):
        buffer = event.app.current_buffer
        if buffer.complete_state:
            buffer.complete_next()
        else:
            buffer.start_completion(select_first=False)

    # ── 补全翻页（PageUp/PageDown）──
    @_add("completion_page_up")
    def _(event):
        buffer = event.app.current_buffer
        if buffer.complete_state:
            buffer.complete_previous_page()

    @_add("completion_page_down")
    def _(event):
        buffer = event.app.current_buffer
        if buffer.complete_state:
            buffer.complete_next_page()

    # ── Tab / Shift+Tab ──
    if use_dropdown_menu:
        @_add("completion_next")
        def _(event):
            buffer = event.app.current_buffer
            # 清除 ghost suggestion，防止虚影残留与补全叠加导致文本损坏
            buffer.suggestion = None
            if buffer.complete_state:
                buffer.complete_next()
            else:
                buffer.start_completion(select_first=False)
    else:
        @_add("completion_next")
        def _(event):
            """关闭下拉菜单：Tab 直接内联接受第一个补全。"""
            buffer = event.app.current_buffer
            buffer.suggestion = None
            try:
                doc = buffer.document
                completions = list(buffer.completer.get_completions(doc, None))
            except Exception:
                completions = []
            if not completions:
                return
            c = completions[0]
            try:
                buffer.apply_completion(c)
            except Exception:
                # 兜底：手工替换
                sp = getattr(c, "start_position", 0) or 0
                ins = doc.cursor_position + sp
                if ins < 0:
                    ins = 0
                buffer.text = doc.text[:ins] + c.text + doc.text[doc.cursor_position:]
                buffer.cursor_position = ins + len(c.text)

    @_add("completion_prev")
    def _(event):
        buffer = event.app.current_buffer
        if buffer.complete_state:
            buffer.complete_previous()
        else:
            buffer.start_completion(select_first=False)

    # ── 手动触发补全 ──
    @_add("completion_trigger")
    def _(event):
        buffer = event.app.current_buffer
        buffer.start_completion(select_first=False)

    @_add("completion_alt_next")
    def _(event):
        buffer = event.app.current_buffer
        if buffer.complete_state:
            buffer.complete_next()
        else:
            buffer.start_completion(select_first=False)

    @_add("completion_alt_prev")
    def _(event):
        buffer = event.app.current_buffer
        if buffer.complete_state:
            buffer.complete_previous()
        else:
            buffer.start_completion(select_first=False)

    # ── 清屏 ──
    @_add("clear_screen")
    def _(event):
        if terminal_type in ("cmd", "powershell") or (sys_type == "Windows" and terminal_type in ("", "cmd")):
            os.system('cls')
        else:
            os.system('clear')
        event.app.renderer.reset()

    # ── 回车：路径补全时接受目录并级联继续补全 ──
    @Condition
    def is_dir_completion():
        try:
            app = get_app()
            buffer = app.current_buffer
            if buffer.complete_state:
                cc = buffer.complete_state.current_completion
                if cc and (cc.text.endswith('/') or cc.text.endswith(os.sep)):
                    return True
        except Exception:
            pass
        return False

    @kb.add('enter', filter=is_dir_completion)
    def _(event):
        buffer = event.app.current_buffer
        cc = buffer.complete_state.current_completion
        if cc:
            buffer.apply_completion(cc)
            buffer.start_completion(select_first=False)

    # ── ESC+Space：全局切换补全锁定 ──
    @_add("completion_lock")
    def _(event):
        global _completion_locked
        buffer = event.app.current_buffer
        if buffer.complete_state:
            buffer.cancel_completion()
        _completion_locked = not _completion_locked

    # ── 右键：接受虚影 ──
    @kb.add('right')
    def _(event):
        buffer = event.app.current_buffer
        if buffer.suggestion:
            sug_text = buffer.suggestion.text
            buffer.suggestion = None
            if '\n' in sug_text:
                input_lib_module._PENDING_MULTILINE_RECALL = buffer.text + sug_text
                buffer.text = ""
                buffer.cursor_position = 0
            else:
                buffer.insert_text(sug_text)
        else:
            pos = buffer.cursor_position
            if pos < len(buffer.text):
                buffer.cursor_position = pos + 1

    # ── Alt+Enter：进入独立全屏多行编辑区 ──
    @_add("multiline_editor")
    def _(event):
        buffer = event.app.current_buffer
        input_lib_module._request_multiline_editor(buffer.text)
        event.app.exit(result=input_lib_module.MULTILINE_EDITOR_SENTINEL)

    return kb


# 主 REPL 可配置动作表：(动作 id, ptk.json 里的键名, 中文说明, English)
# 供 `config-onyx-repl keys` 与 TUI 按键设置界面使用。
REPL_KEY_ACTIONS = [
    ("history_up", "history_up", "历史上一条", "History previous"),
    ("history_down", "history_down", "历史下一条", "History next"),
    ("prefix_history_up", "prefix_history_up", "前缀历史上一条（Alt+↑）", "Prefix history prev"),
    ("prefix_history_down", "prefix_history_down", "前缀历史下一条（Alt+↓）", "Prefix history next"),
    ("completion_next", "completion_next", "补全下一项", "Completion next"),
    ("completion_prev", "completion_prev", "补全上一项", "Completion previous"),
    ("completion_page_up", "completion_page_up", "补全菜单上翻页", "Completion page up"),
    ("completion_page_down", "completion_page_down", "补全菜单下翻页", "Completion page down"),
    ("completion_menu_up", "completion_menu_up", "补全菜单选择：上", "Completion menu up"),
    ("completion_menu_down", "completion_menu_down", "补全菜单选择：下", "Completion menu down"),
    ("completion_trigger", "completion_trigger", "手动触发补全", "Trigger completion"),
    ("completion_alt_next", "completion_alt_next", "补全下一项（备用键）", "Completion next (alt)"),
    ("completion_alt_prev", "completion_alt_prev", "补全上一项（备用键）", "Completion prev (alt)"),
    ("completion_lock", "completion_lock", "切换补全锁定", "Toggle completion lock"),
    ("clear_screen", "clear_screen", "清屏", "Clear screen"),
    ("multiline_editor", "multiline_editor", "进入全屏多行编辑区", "Open multi-line editor"),
]