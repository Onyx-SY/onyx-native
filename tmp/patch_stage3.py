# -*- coding: utf-8 -*-
"""Stage3 补丁：计划确认弹窗（正文与询问同图层、可滚动、屏幕自适应）。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TUI = os.path.join(ROOT, "bin", "ai_tui.py")
UI = os.path.join(ROOT, "bin", "ai_lib", "ui.py")
HELPERS = os.path.join(ROOT, "bin", "ai_lib", "helpers.py")

PATCHES = []


def patch(path, old, new):
    PATCHES.append((path, old, new))


# ────────────────── ai_tui.py：新增 PlanConfirmScreen ──────────────────
patch(
    TUI,
    """    class TextScreen(ModalScreen):
        BINDINGS = [Binding("escape", "cancel", "Cancel")]""",
    """    class PlanConfirmScreen(ModalScreen):
        \"\"\"计划确认：**计划正文与选项同处一个弹窗**，正文区可上下滚动。

        旧实现先把计划 console.print 到主屏，再压一个 SelectScreen 上去：
          - 计划正文落在 70% 不透明的遮罩下面 → 基本看不清；
          - 弹窗里没有任何滚动容器（Vertical 默认 overflow:hidden）→ 超长计划直接被裁掉；
          - 弹窗宽度/高度写死百分比，手机窄屏下更难看。
        这里把正文搬进弹窗，正文区高度按「屏幕高度 + 计划行数」自适应，超出即可滚动。
        \"\"\"

        BINDINGS = [
            Binding("escape", "cancel", "Cancel", priority=True),
            Binding("pageup", "body_up", "Scroll up", show=False, priority=True),
            Binding("pagedown", "body_down", "Scroll down", show=False, priority=True),
        ]

        def __init__(self, message: str, options, default: str = "",
                     body: str = "", lang: str = "chinese"):
            super().__init__()
            self.message = message
            self.options = list(options)
            self.default = default
            self.body = body or ""
            self.lang = lang

        def _body_widget(self):
            \"\"\"正文优先用 Textual 的 Markdown 控件（可滚动、带高亮），失败则退 Rich Markdown。\"\"\"
            try:
                from textual.widgets import Markdown as _MD
                return _MD(self.body)
            except Exception:
                try:
                    from rich.markdown import Markdown as _RMD
                    return Static(_RMD(self.body))
                except Exception:
                    return Static(self.body)

        def compose(self):
            cn = self.lang != "english"
            with Vertical(id="modal"):
                yield Static(self.message, id="modal-msg")
                yield Static(("≡ 计划" if cn else "≡ Plan"), id="modal-title")
                from textual.containers import VerticalScroll
                with VerticalScroll(id="modal-body"):
                    yield self._body_widget()
                yield OptionList(*self.options, id="modal-list")

        def on_mount(self):
            # 正文区高度自适应：屏幕越大 / 计划越长，展示越多；手机上也不会把选项挤出屏幕。
            try:
                lines = self.body.count("\\n") + 1
                screen_h = int(getattr(self.app.size, "height", 24) or 24)
                avail = max(3, screen_h - 11)          # 消息 + 标题 + 选项 + 边框内边距
                h = max(3, min(lines + 1, 18, avail))
                self.query_one("#modal-body").styles.height = h
            except Exception:
                pass
            ol = self.query_one("#modal-list", OptionList)
            ol.focus()
            if self.default in self.options:
                ol.highlighted = self.options.index(self.default)

        def action_body_up(self):
            try:
                self.query_one("#modal-body").scroll_page_up(animate=False)
            except Exception:
                pass

        def action_body_down(self):
            try:
                self.query_one("#modal-body").scroll_page_down(animate=False)
            except Exception:
                pass

        def on_option_list_option_selected(self, event):
            self.dismiss(str(event.option.prompt))

        def action_cancel(self):
            # Esc = 用户明确取消 → 送回哨兵（绝不能 dismiss(default)：计划默认项就是「确认」）
            self.dismiss(_CANCEL)

    class TextScreen(ModalScreen):
        BINDINGS = [Binding("escape", "cancel", "Cancel")]""",
)

# 适配器：body 参数贯通
patch(
    TUI,
    """        def select_option(self, message, options, default="", lang="chinese", blocking=False):
            # blocking=True（计划确认）：不设超时，且把「Esc 取消」原样透出，
            # 让 confirm_plan 能区分「用户取消（重新询问）」与「交互不可用（安全收尾）」。
            r = self._modal(SelectScreen(message, options, default),""",
    """        def select_option(self, message, options, default="", lang="chinese",
                          blocking=False, body=""):
            # blocking=True（计划确认）：不设超时，且把「Esc 取消」原样透出，
            # 让 confirm_plan 能区分「用户取消（重新询问）」与「交互不可用（安全收尾）」。
            # body 非空 → 用「正文 + 选项同图层」的计划弹窗（可滚动）。
            screen = (PlanConfirmScreen(message, options, default, body=body, lang=lang)
                      if body else SelectScreen(message, options, default))
            r = self._modal(screen,""",
)

# CSS：遮罩降不透明度 + 正文滚动区 + 窄屏宽度
patch(
    TUI,
    """        /* 模态框 */
        ModalScreen { align: center middle; background: $background 70%; }
        #modal { width: 80%; max-width: 96; height: auto; max-height: 80%;
                 padding: 1 2; background: $panel; border: round $accent; }""",
    """        /* 模态框：遮罩压暗但不遮内容（55%）；宽度按窄屏优化（手机 40 列也能看全）*/
        ModalScreen { align: center middle; background: $background 55%; }
        #modal { width: 90%; max-width: 96; height: auto; max-height: 88%;
                 padding: 1 2; background: $panel; border: round $accent; }
        /* 计划确认：正文与选项同处一个弹窗；正文区可滚动（高度在 on_mount 自适应）*/
        #modal-title { color: $onyx-muted; text-style: bold; padding: 0 0 0 1; }
        #modal-body { height: 6; margin: 0 0 1 0; padding: 0 1; background: $surface;
                      border-top: solid $onyx-rule; border-bottom: solid $onyx-rule;
                      scrollbar-size-vertical: 1; scrollbar-color: $onyx-rail;
                      scrollbar-background: $surface; }
        #modal-list { height: auto; max-height: 6; }""",
)

# ────────────────── ui.py：select_option 增 body 参数 ──────────────────
patch(
    UI,
    """def select_option(
    message: str,
    options: List[str],
    default: str = "",
    lang: str = "chinese",
    blocking: bool = False,
) -> str:
    \"\"\"
    箭头键选择菜单。
    
    参数:
      message: 提示语
      options: 选项列表（按顺序，第一项为默认）
      default: 默认选项（为空则取 options[0]）
      lang: 语言
    
    返回: 用户选择的选项字符串
    \"\"\"
    _adapter = get_ui_adapter()
    if _adapter is not None and hasattr(_adapter, "select_option"):
        try:
            return _adapter.select_option(message, options, default, lang, blocking=blocking)
        except TypeError:
            # 旧适配器不认 blocking → 退回旧签名（仅兜底，TUI 适配器已支持）
            return _adapter.select_option(message, options, default, lang)""",
    """def select_option(
    message: str,
    options: List[str],
    default: str = "",
    lang: str = "chinese",
    blocking: bool = False,
    body: str = "",
) -> str:
    \"\"\"
    箭头键选择菜单。
    
    参数:
      message: 提示语
      options: 选项列表（按顺序，第一项为默认）
      default: 默认选项（为空则取 options[0]）
      lang: 语言
      blocking: 阻塞式（人工决策，不设超时）
      body: 可选的正文（长文本）：TUI 下与选项渲染在**同一个弹窗**里并可滚动
            （计划确认用；REPL 下调用方自行 print，此参数被忽略）
    
    返回: 用户选择的选项字符串
    \"\"\"
    _adapter = get_ui_adapter()
    if _adapter is not None and hasattr(_adapter, "select_option"):
        try:
            return _adapter.select_option(message, options, default, lang,
                                          blocking=blocking, body=body)
        except TypeError:
            try:
                # 旧适配器不认 body → 退回仅 blocking 的签名
                return _adapter.select_option(message, options, default, lang, blocking=blocking)
            except TypeError:
                # 再旧的适配器 → 原始签名（仅兜底）
                return _adapter.select_option(message, options, default, lang)""",
)

# ────────────────── helpers.py：TUI 下不再重复 print 计划 ──────────────────
patch(
    HELPERS,
    """    旧实现把这些情况统统 `return "confirm"`，导致「计划还没确认，AI 就已经开始执行」。
    \"\"\"
    console.print(render_plan_panel(plan_text))
    console.print()""",
    """    旧实现把这些情况统统 `return "confirm"`，导致「计划还没确认，AI 就已经开始执行」。
    \"\"\"
    # TUI：计划正文由确认弹窗自带（与选项同图层、可滚动）→ 这里再 print 一次会
    # 把正文塞进被遮罩压暗的主屏，既重复又看不清。
    _tui = False
    try:
        from bin.ai_lib.mode import is_tui_render as _is_tui_render
        _tui = bool(_is_tui_render())
    except Exception:
        _tui = False
    if not _tui:
        console.print(render_plan_panel(plan_text))
        console.print()""",
)

patch(
    HELPERS,
    """                default=_opt_confirm,
                lang=get_current_lang(),
                blocking=True,   # 人工决策，不设超时：必须等到明确选择
            )""",
    """                default=_opt_confirm,
                lang=get_current_lang(),
                blocking=True,   # 人工决策，不设超时：必须等到明确选择
                body=plan_text,  # TUI：正文与选项同处一个弹窗（REPL 忽略）
            )""",
)


def main():
    cache = {}
    for path, old, new in PATCHES:
        if path not in cache:
            with open(path, encoding="utf-8") as f:
                cache[path] = f.read()
        text = cache[path]
        n = text.count(old)
        if n != 1:
            print(f"FAIL {os.path.relpath(path, ROOT)}: 命中 {n} 次（期望 1）")
            print(old[:300])
            return 1
        cache[path] = text.replace(old, new, 1)
        print(f"OK   {os.path.relpath(path, ROOT)}: {len(old)}B → {len(new)}B")
    for path, text in cache.items():
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    print("PATCH_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
