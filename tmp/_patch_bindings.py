# -*- coding: utf-8 -*-
"""把 ai_tui.py 的两处硬编码 BINDINGS 换成 keymap 注册表驱动（机械替换，带校验）。"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "bin", "ai_tui.py")
src = io.open(P, encoding="utf-8").read()

OLD_COMPLETE = '''        BINDINGS = [
            Binding("tab", "menu_accept", "Complete", priority=True, show=False),
            Binding("down", "menu_down", "Next", priority=True, show=False),
            Binding("up", "menu_up", "Prev", priority=True, show=False),
            Binding("escape", "menu_close", "Close", priority=True, show=False),
        ]'''

NEW_COMPLETE = '''        # 补全菜单键：接受 / 上下选择来自 keymap 注册表（可在 /config 改键）；
        # escape 关闭属于菜单语义，固定不改。
        BINDINGS = (
            _kb("complete.accept", "menu_accept", "Complete", priority=True, show=False)
            + _kb("complete.next", "menu_down", "Next", priority=True, show=False)
            + _kb("complete.prev", "menu_up", "Prev", priority=True, show=False)
            + [Binding("escape", "menu_close", "Close", priority=True, show=False)]
        )'''

OLD_MAIN = '''        BINDINGS = [Binding("ctrl+c", "cancel", "Cancel", show=False, priority=True),
                    Binding("ctrl+q", "quit", "Quit"),
                    Binding("ctrl+d", "eof_quit", "Quit", priority=True),
                    Binding("ctrl+p", "menu_up", "History prev", show=False, priority=True),
                    Binding("ctrl+n", "menu_down", "History next", show=False, priority=True),
                    Binding("ctrl+r", "hist_search", "History search", show=False, priority=True),
                    Binding("ctrl+l", "clear_log", "Clear log", show=False, priority=True),
                    Binding("alt+b", "word_left", "Word left", show=False),
                    Binding("alt+f", "word_right", "Word right", show=False),
                    Binding("pageup", "log_page_up", "Scroll up", show=False, priority=True),
                    Binding("pagedown", "log_page_down", "Scroll down", show=False, priority=True),
                    Binding("alt+enter", "to_multiline", "Multiline", show=False),
                    # 字节层把 ESC+CR / ESC+LF 改写成了 CSI-u shift+enter（见 _ALT_ENTER_CSI_U）
                    Binding("shift+enter", "to_multiline", "Multiline", show=False),
                    Binding("alt+ctrl+j", "to_multiline", "Multiline", show=False),
                    Binding("ctrl+alt+j", "to_multiline", "Multiline", show=False)]'''

NEW_MAIN = '''        # 主界面键位全部来自 keymap 注册表（用户可在 /config → 按键设置 里改）。
        # 默认值与历史行为完全一致。
        # 注：字节层把 ESC+CR / ESC+LF 改写成了 CSI-u shift+enter（见 _ALT_ENTER_CSI_U）
        BINDINGS = (
            _kb("tui.cancel", "cancel", "Cancel", show=False, priority=True)
            + _kb("tui.quit", "quit", "Quit")
            + _kb("tui.eof_quit", "eof_quit", "Quit", priority=True)
            + _kb("tui.history_prev", "menu_up", "History prev", show=False, priority=True)
            + _kb("tui.history_next", "menu_down", "History next", show=False, priority=True)
            + _kb("tui.history_search", "hist_search", "History search", show=False, priority=True)
            + _kb("tui.clear_log", "clear_log", "Clear log", show=False, priority=True)
            + _kb("tui.word_left", "word_left", "Word left", show=False)
            + _kb("tui.word_right", "word_right", "Word right", show=False)
            + _kb("tui.scroll_up", "log_page_up", "Scroll up", show=False, priority=True)
            + _kb("tui.scroll_down", "log_page_down", "Scroll down", show=False, priority=True)
            + _kb("tui.multiline", "to_multiline", "Multiline", show=False)
        )'''

for name, old, new in (("complete", OLD_COMPLETE, NEW_COMPLETE),
                       ("main", OLD_MAIN, NEW_MAIN)):
    n = src.count(old)
    if n != 1:
        print(f"FAIL {name}: 命中 {n} 次，期望 1 次")
        sys.exit(1)
    src = src.replace(old, new, 1)
    print(f"OK   {name}: 已替换")

tmp = P + ".tmp"
with io.open(tmp, "w", encoding="utf-8") as f:
    f.write(src)
os.replace(tmp, P)
print("已写回", P)
