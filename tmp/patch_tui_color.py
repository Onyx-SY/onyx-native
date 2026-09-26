# -*- coding: utf-8 -*-
"""修复：① TUI 工具输出丢色（首轮 console 无 color_system）；② 工具块上下留空行。"""
import io

# ══════════════ A) ai_tui.py：拦 Console 构造器，从源头保证有色 ══════════════
P1 = "bin/ai_tui.py"
s1 = io.open(P1, encoding="utf-8").read()

HOOK = '''

def _install_console_color_hook(app) -> None:
    """TUI：让「之后创建」的每个 Rich Console 都带颜色并对齐日志区宽度。

    为什么必须从构造器入手：各模块的 `console = Console()` 是**模块级**对象，而引擎
    模块（ai_cmd / tool_executors / mcp_exec / …）是在 `sys.stdout` 已被换成
    `_QueueStream`（非终端）之后才惰性 import 的 —— Rich 在构造时就把 color_system
    判成 None，之后 `console.print` 一律不产生 ANSI，日志区里工具名、参数、结果、
    diff 全部退化成纯白文本，与 REPL 的彩色输出完全脱节（用户观感：糊成一片白）。

    `_sync_rich_width` 的 gc 收集只能补救「已加载」的 console，而 ai_cmd 是在
    `_call_ai_engine` **内部**才 import 的，同步时机永远追不上 → 这里直接拦构造器，
    从源头保证首轮就正确。

    排除 textual 自身创建的 Console：它们的终端/颜色由 Textual 管理，强行改写会影响
    截图与渲染。
    """
    try:
        from rich.console import Console as _C, ColorSystem as _CS
    except Exception:
        return
    if getattr(_C, "_onyx_tui_hooked", False):
        return
    _orig_init = _C.__init__

    def _hooked_init(self, *args, **kwargs):
        _orig_init(self, *args, **kwargs)
        try:
            if not _mode.is_tui_render():
                return
            # 跳过 Textual 自己的 Console（截图 / 渲染用，不能动）
            if str(sys._getframe(1).f_globals.get("__name__", "")).startswith("textual"):
                return
            if kwargs.get("force_terminal") is None:
                self._force_terminal = True
            if kwargs.get("color_system") in (None, "auto") and getattr(self, "_color_system", None) is None:
                self._color_system = _CS.TRUECOLOR
            # 宽度：模块级 console 默认按「整屏」算，日志区比整屏窄（宽屏还要减侧栏），
            # 不同步会换行错乱。只读缓存值（主线程 _measure 写入），worker 线程安全。
            if kwargs.get("width") is None:
                _w = int(getattr(app, "_log_w", 0) or 0)
                if _w > 8:
                    self._width = _w
                    self._height = int(getattr(app, "_log_h", 24) or 24)
        except Exception:
            pass

    try:
        _C.__init__ = _hooked_init
        _C._onyx_tui_hooked = True
    except Exception:
        pass


def _ai_history_path(user_home_dir: str) -> str:'''

old_anchor = '''

def _ai_history_path(user_home_dir: str) -> str:'''
assert s1.count(old_anchor) == 1, ("hook anchor", s1.count(old_anchor))
s1 = s1.replace(old_anchor, HOOK)

old_call = '''                try:
                    self.call_from_thread(self._measure)
                except Exception:
                    pass
                self._sync_rich_width(full=True)'''
new_call = '''                try:
                    self.call_from_thread(self._measure)
                except Exception:
                    pass
                self._sync_rich_width(full=True)
                # 引擎模块是在 _call_ai_engine **内部**才 import 的 → gc 同步追不上，
                # 必须拦 Console 构造器，才能保证首轮的工具输出就带颜色、宽度正确。
                _install_console_color_hook(self)'''
assert s1.count(old_call) == 1, ("hook call", s1.count(old_call))
s1 = s1.replace(old_call, new_call)

io.open(P1, "w", encoding="utf-8").write(s1)
print("✅ ai_tui.py 颜色钩子")

# ══════════════ B) ai_cmd.py：TUI 下工具块上下各留一空行 ══════════════
P2 = "bin/ai_cmd.py"
s2 = io.open(P2, encoding="utf-8").read()

old_b1 = '''                    console.print(f"  [bold green]🔧 {_tool_display_name}{_agent_mark}{_tag}[/]{_param_preview}")'''
new_b1 = '''                    if _tui_mode:
                        # TUI 无面板框线 → 用空行把每个工具块与上下文隔开（REPL 有框，不加）
                        console.print("")
                    console.print(f"  [bold green]🔧 {_tool_display_name}{_agent_mark}{_tag}[/]{_param_preview}")'''
assert s2.count(old_b1) == 1, ("tool blank before", s2.count(old_b1))
s2 = s2.replace(old_b1, new_b1)

old_b2 = '''                    else:
                        err_msg = _mcp_t(f"❌ 工具执行失败: {output}", f"❌ Tool execution failed: {output}")
                        tool_results.append(err_msg)
                        console.print(f"   {err_msg}", style="bold red")
'''
new_b2 = '''                    else:
                        err_msg = _mcp_t(f"❌ 工具执行失败: {output}", f"❌ Tool execution failed: {output}")
                        tool_results.append(err_msg)
                        console.print(f"   {err_msg}", style="bold red")
                    if _tui_mode and _tc_i == len(tool_calls) - 1:
                        # 只在本轮最后一个工具后收尾，避免与下一块的「前置空行」叠成两行
                        console.print("")
'''
assert s2.count(old_b2) == 1, ("tool blank after", s2.count(old_b2))
s2 = s2.replace(old_b2, new_b2)

io.open(P2, "w", encoding="utf-8").write(s2)
print("✅ ai_cmd.py 工具块间距")
