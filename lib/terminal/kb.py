# lib/terminal/kb.py
"""
键盘绑定模块
提供上下键历史导航、前缀历史导航（Alt+上下）、补全菜单选择等绑定
支持从 ptk.json 配置加载键位
修复：右键逐项补全（Tab 键下一项，Shift+Tab 上一项）
新增：鼠标点击补全支持（由 prompt 的 mouse_support=True 提供） - 已移除，改用键盘导航
新增：补全菜单翻页键绑定（PageUp/PageDown）
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


def is_completion_locked() -> bool:
    """返回当前补全是否被全局锁定"""
    return _completion_locked


def create_key_bindings(sys_type: str = "", terminal_type: str = "bash", ptk_config: Dict[str, Any] = None) -> KeyBindings:
    """
    创建并返回 prompt_toolkit 的 KeyBindings 对象。
    
    Args:
        sys_type: 系统类型 (Windows/Linux/Mac)
        terminal_type: 终端类型 (bash/cmd/powershell/zsh/fish)
        ptk_config: 来自 ptk.json 的配置字典
    """
    from . import input_lib as input_lib_module

    kb = KeyBindings()

    def _add(action_key: str):
        """按 default_keys（来自 ptk.json）绑定 handler；支持 'escape, space' 多键序列。"""
        raw = default_keys.get(action_key) or ""
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if not parts:
            return lambda f: f
        try:
            return kb.add(*parts)
        except Exception:
            return lambda f: f

    # 默认键位映射
    default_keys = {
        "history_up": "up",
        "history_down": "down",
        "prefix_history_up": "escape, up",
        "prefix_history_down": "escape, down",
        "completion_next": "tab",
        "completion_prev": "s-tab",
        "clear_screen": "c-l",
        "completion_page_up": "pageup",
        "completion_page_down": "pagedown"
    }

    # 从 ptk_config 中加载键位，若缺失则使用默认值
    if ptk_config and "key_bindings" in ptk_config:
        user_keys = ptk_config["key_bindings"]
        for key in default_keys:
            if key in user_keys:
                default_keys[key] = user_keys[key]

    # 普通上下键：永远遍历全部历史（不接管补全菜单 — 补全选择用 Ctrl+Up/Down）
    @kb.add(default_keys["history_up"])
    def _(event):
        buffer = event.app.current_buffer
        new_text, new_pos = input_lib_module.handle_up_arrow_normal(buffer.text)
        if new_text != buffer.text:
            buffer.text = new_text
            buffer.cursor_position = new_pos

    @kb.add(default_keys["history_down"])
    def _(event):
        buffer = event.app.current_buffer
        new_text, new_pos = input_lib_module.handle_down_arrow_normal(buffer.text)
        if new_text != buffer.text:
            buffer.text = new_text
            buffer.cursor_position = new_pos

    # Alt+上下键 / Shift+上下键：根据前缀历史导航
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

    # 如果用户自定义了前缀导航的键位，我们额外绑定它们
    prefix_up_keys = default_keys.get("prefix_history_up", "")
    if prefix_up_keys and prefix_up_keys not in ("escape, up", "s-up"):
        for key_combo in [prefix_up_keys]:
            @kb.add(*key_combo.split(','))
            def custom_prefix_up(event):
                buffer = event.app.current_buffer
                if buffer.complete_state:
                    buffer.cancel_completion()
                new_text, new_pos = input_lib_module.handle_up_arrow_with_prefix(buffer.text)
                if new_text != buffer.text:
                    buffer.text = new_text
                    buffer.cursor_position = new_pos

    prefix_down_keys = default_keys.get("prefix_history_down", "")
    if prefix_down_keys and prefix_down_keys not in ("escape, down", "s-down"):
        for key_combo in [prefix_down_keys]:
            @kb.add(*key_combo.split(','))
            def custom_prefix_down(event):
                buffer = event.app.current_buffer
                if buffer.complete_state:
                    buffer.cancel_completion()
                new_text, new_pos = input_lib_module.handle_down_arrow_with_prefix(buffer.text)
                if new_text != buffer.text:
                    buffer.text = new_text
                    buffer.cursor_position = new_pos

    # Ctrl+上下键：补全菜单选择
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

    # 补全翻页（PageUp/PageDown）
    @kb.add(default_keys["completion_page_up"])
    def _(event):
        buffer = event.app.current_buffer
        if buffer.complete_state:
            # prompt_toolkit 内置的补全菜单翻页支持
            buffer.complete_previous_page()

    @kb.add(default_keys["completion_page_down"])
    def _(event):
        buffer = event.app.current_buffer
        if buffer.complete_state:
            buffer.complete_next_page()

    # 补全下一项/上一项（Tab/Shift+Tab）
    @kb.add(default_keys["completion_next"])
    def _(event):
        buffer = event.app.current_buffer
        # 清除 ghost suggestion，防止虚影残留与补全叠加导致文本损坏
        buffer.suggestion = None
        if buffer.complete_state:
            buffer.complete_next()
        else:
            buffer.start_completion(select_first=False)

    @kb.add(default_keys["completion_prev"])
    def _(event):
        buffer = event.app.current_buffer
        if buffer.complete_state:
            buffer.complete_previous()
        else:
            buffer.start_completion(select_first=False)

    # 手动触发补全
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

    # 清屏
    @kb.add(default_keys["clear_screen"])
    def _(event):
        # cls / clear：cmd/powershell 用 cls；bash 会话即使运行在 Windows 上也用 clear
        if terminal_type in ("cmd", "powershell") or (sys_type == "Windows" and terminal_type in ("", "cmd")):
            os.system('cls')
        else:
            os.system('clear')
        event.app.renderer.reset()

    # 回车：路径补全时接受目录并级联继续补全；非目录补全走默认提交
    @Condition
    def is_dir_completion():
        """仅当补全菜单打开且当前选中项为目录时返回 True"""
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

    # ESC+Space：全局切换补全锁定（锁住后输入不弹补全，再按解锁）
    @_add("completion_lock")
    def _(event):
        global _completion_locked
        buffer = event.app.current_buffer
        # 如果当前菜单打开，先关闭
        if buffer.complete_state:
            buffer.cancel_completion()
        # 翻转全局锁定状态
        _completion_locked = not _completion_locked

    # 右键：接受虚影（complete_while_typing 自动处理后续补全刷新）
    @kb.add('right')
    def _(event):
        buffer = event.app.current_buffer
        if buffer.suggestion:
            sug_text = buffer.suggestion.text
            buffer.suggestion = None
            if '\n' in sug_text:
                # 多行命令虚影：不能塞进单行缓冲区（会显示成 ^J 或被压成一行），
                # 记录待重放，清空缓冲区；回车后以多行形式补全并执行
                input_lib_module._PENDING_MULTILINE_RECALL = buffer.text + sug_text
                buffer.text = ""
                buffer.cursor_position = 0
            else:
                buffer.insert_text(sug_text)
        else:
            pos = buffer.cursor_position
            if pos < len(buffer.text):
                buffer.cursor_position = pos + 1

    # Alt+Enter：进入独立全屏多行编辑区（键名可在 ptk.json 里改）
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