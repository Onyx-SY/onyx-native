# -*- coding: utf-8 -*-
"""step-4b + step-5：TUI 补全下拉菜单（Tab/↑↓/Esc）+ Ctrl+C 取消语义。

- 新增 PromptInput(Input)：Tab=补全、↑↓=选择、Esc=关闭（priority 键位，盖过 Screen 的 tab 焦点切换）
- 新增 CompletionMenu(OptionList)：悬浮在输入框上方，候选来自与 REPL 共用的 completion_candidates()
- ctrl+c 由 quit 改为 cancel：忙 → 取消当前生成（置 _AI_INTERRUPTED + abort_active_response）；
  闲 → 清空输入框；退出仍为 Ctrl+Q / Ctrl+D

用法：python3 tmp/patch_tui_completion.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "bin", "ai_tui.py")

WIDGETS = '''    # ────────────────────────── 输入控件（续） ──────────────────────────
    class PromptInput(Input):
        """单行输入框 + 补全菜单键位。

        priority=True 盖过 Screen 默认的 tab=焦点切换 / Input 自带的 up/down，
        动作统一委托给 App（菜单状态在 App 里维护）。
        """

        BINDINGS = [
            Binding("tab", "menu_accept", "Complete", priority=True, show=False),
            Binding("down", "menu_down", "Next", priority=True, show=False),
            Binding("up", "menu_up", "Prev", priority=True, show=False),
            Binding("escape", "menu_close", "Close", priority=True, show=False),
        ]

        def action_menu_accept(self) -> None:
            self.app.action_complete_accept()

        def action_menu_down(self) -> None:
            self.app.action_menu_down()

        def action_menu_up(self) -> None:
            self.app.action_menu_up()

        def action_menu_close(self) -> None:
            self.app.action_menu_close()

    class CompletionMenu(OptionList):
        """输入框上方的补全下拉菜单（默认隐藏）。"""

    # ────────────────────────── 主 App ──────────────────────────
    class OnyxTUI(App):'''

REPLACEMENTS = [
    # 1) 导入 Option + completion_candidates 用到的类
    (
        "    from textual.widgets import (Header, Input, Static, RichLog, Button, OptionList,\n"
        "                                 DirectoryTree, TextArea)\n",
        "    from textual.widgets import (Header, Input, Static, RichLog, Button, OptionList,\n"
        "                                 DirectoryTree, TextArea, Option)\n",
    ),
    # 2) 插入两个控件类
    (
        "    # ────────────────────────── 主 App ──────────────────────────\n"
        "    class OnyxTUI(App):\n",
        WIDGETS + "\n",
    ),
    # 3) CSS：菜单样式
    (
        "        #prompt-hint { height: 1; color: $text-muted; }\n",
        "        #prompt-hint { height: 1; color: $text-muted; }\n"
        "        #complete-menu { display: none; height: auto; max-height: 8;\n"
        "                         border: round $accent; background: $surface; }\n",
    ),
    # 4) 键位：ctrl+c 改 cancel
    (
        '        BINDINGS = [Binding("ctrl+c", "quit", "Quit"), Binding("ctrl+q", "quit", "Quit"),\n',
        '        BINDINGS = [Binding("ctrl+c", "cancel", "Cancel", show=False),\n'
        '                    Binding("ctrl+q", "quit", "Quit"),\n',
    ),
    # 5) compose：单行框换成 PromptInput + 菜单
    (
        "            with Vertical(id=\"prompt-wrap\"):\n"
        "                yield Static(_t(\"tui_single_hint\", self._lang), id=\"prompt-hint\")\n"
        "                yield Input(placeholder=_t(\"tui_placeholder\", self._lang), id=\"prompt\",\n"
        "                            suggester=self._suggester)\n",
        "            with Vertical(id=\"prompt-wrap\"):\n"
        "                yield CompletionMenu(id=\"complete-menu\")\n"
        "                yield Static(_t(\"tui_single_hint\", self._lang), id=\"prompt-hint\")\n"
        "                yield PromptInput(placeholder=_t(\"tui_placeholder\", self._lang), id=\"prompt\",\n"
        "                                  suggester=self._suggester)\n",
    ),
    # 6) __init__：菜单候选缓存
    (
        "            self._lang = (ctx or {}).get(\"lang\", \"chinese\") or \"chinese\"\n",
        "            self._lang = (ctx or {}).get(\"lang\", \"chinese\") or \"chinese\"\n"
        "            self._menu_cands = []      # 当前补全候选 [(插入文本, 描述, 替换长度)]\n",
    ),
    # 7) 提交时收起菜单 + Ctrl+C 取消 + 补全菜单动作（挂在 _submit 之前）
    (
        "        def on_input_submitted(self, event):\n"
        "            \"\"\"单行框：Enter 发送（虚影由 → 接受）。\"\"\"\n"
        "            try:\n"
        "                self.query_one(\"#prompt\", Input).value = \"\"\n",
        "        def on_input_submitted(self, event):\n"
        "            \"\"\"单行框：Enter 发送（虚影由 → 接受）。\"\"\"\n"
        "            self._menu_hide()\n"
        "            try:\n"
        "                self.query_one(\"#prompt\", Input).value = \"\"\n",
    ),
    (
        "        def _set_hint(self, key: str) -> None:\n",
        "        # ── 补全菜单（候选与 REPL 共用 completion_candidates）──\n"
        "        def _menu_label(self, value: str, meta: str):\n"
        "            from rich.text import Text as _T\n"
        "            t = _T(value, style=\"bold\")\n"
        "            if meta:\n"
        "                t.append(\"  \" + meta, style=\"dim\")\n"
        "            return t\n"
        "\n"
        "        def _menu_hide(self) -> None:\n"
        "            try:\n"
        "                self.query_one(\"#complete-menu\", CompletionMenu).display = False\n"
        "            except Exception:\n"
        "                pass\n"
        "            self._menu_cands = []\n"
        "\n"
        "        def _menu_update(self, text: str) -> None:\n"
        "            \"\"\"按当前输入刷新补全菜单（输入 / 或路径时才出现）。\"\"\"\n"
        "            try:\n"
        "                menu = self.query_one(\"#complete-menu\", CompletionMenu)\n"
        "            except Exception:\n"
        "                return\n"
        "            if not (text or \"\").strip():\n"
        "                self._menu_hide()\n"
        "                return\n"
        "            try:\n"
        "                from bin.ai_interactive import completion_candidates\n"
        "                cands = completion_candidates(text, self._lang)\n"
        "            except Exception:\n"
        "                cands = []\n"
        "            if not cands:\n"
        "                self._menu_hide()\n"
        "                return\n"
        "            self._menu_cands = cands\n"
        "            try:\n"
        "                menu.clear_options()\n"
        "                for i, (value, meta, _rl) in enumerate(cands):\n"
        "                    menu.add_option(Option(self._menu_label(value, meta), id=str(i)))\n"
        "                menu.highlighted = 0\n"
        "                menu.display = True\n"
        "            except Exception:\n"
        "                self._menu_hide()\n"
        "\n"
        "        def on_input_changed(self, event) -> None:\n"
        "            \"\"\"单行框内容变化 → 刷新补全菜单。\"\"\"\n"
        "            try:\n"
        "                if getattr(event.input, \"id\", \"\") == \"prompt\":\n"
        "                    self._menu_update(event.value)\n"
        "            except Exception:\n"
        "                pass\n"
        "\n"
        "        def action_complete_accept(self) -> None:\n"
        "            \"\"\"Tab：接受高亮候选（菜单未开则先按当前输入打开）。\"\"\"\n"
        "            try:\n"
        "                menu = self.query_one(\"#complete-menu\", CompletionMenu)\n"
        "                inp = self.query_one(\"#prompt\", PromptInput)\n"
        "            except Exception:\n"
        "                return\n"
        "            if menu.display and self._menu_cands:\n"
        "                idx = menu.highlighted if isinstance(menu.highlighted, int) else 0\n"
        "                if 0 <= idx < len(self._menu_cands):\n"
        "                    value, _meta, replace_len = self._menu_cands[idx]\n"
        "                    text = inp.value or \"\"\n"
        "                    inp.value = text[: len(text) - replace_len] + value\n"
        "                    try:\n"
        "                        inp.cursor_position = len(inp.value)\n"
        "                    except Exception:\n"
        "                        pass\n"
        "                    self._menu_update(inp.value)\n"
        "                return\n"
        "            self._menu_update(inp.value or \"\")\n"
        "\n"
        "        def action_menu_down(self) -> None:\n"
        "            try:\n"
        "                menu = self.query_one(\"#complete-menu\", CompletionMenu)\n"
        "                if menu.display:\n"
        "                    menu.action_cursor_down()\n"
        "            except Exception:\n"
        "                pass\n"
        "\n"
        "        def action_menu_up(self) -> None:\n"
        "            try:\n"
        "                menu = self.query_one(\"#complete-menu\", CompletionMenu)\n"
        "                if menu.display:\n"
        "                    menu.action_cursor_up()\n"
        "            except Exception:\n"
        "                pass\n"
        "\n"
        "        def action_menu_close(self) -> None:\n"
        "            self._menu_hide()\n"
        "\n"
        "        def action_cancel(self) -> None:\n"
        "            \"\"\"Ctrl+C：忙 → 取消当前生成；闲 → 清空输入框（不再直接退出 TUI）。\n"
        "\n"
        "            取消机制复用引擎已有的中断标志：SSE 循环在下一 chunk 看到\n"
        "            mcp_state._AI_INTERRUPTED 就会返回 _interrupted 并丢弃半截内容；\n"
        "            abort_active_response 关闭底层连接，解锁阻塞中的 iter_lines。\n"
        "            \"\"\"\n"
        "            if self._busy:\n"
        "                try:\n"
        "                    from bin.ai_lib import mcp_state as _ms\n"
        "                    _ms._AI_INTERRUPTED = True\n"
        "                except Exception:\n"
        "                    pass\n"
        "                try:\n"
        "                    from bin.ai_lib.api import abort_active_response\n"
        "                    abort_active_response(self._ctx.get(\"session_id\", \"\"))\n"
        "                except Exception:\n"
        "                    pass\n"
        "                self._log(_t(\"tui_cancelling\", self._lang))\n"
        "                return\n"
        "            try:\n"
        "                inp = self.query_one(\"#prompt\", PromptInput)\n"
        "            except Exception:\n"
        "                return\n"
        "            if (inp.value or \"\").strip():\n"
        "                inp.value = \"\"\n"
        "                self._menu_hide()\n"
        "            else:\n"
        "                self._log(_t(\"tui_ctrlc_hint\", self._lang))\n"
        "\n"
        "        def _set_hint(self, key: str) -> None:\n",
    ),
]


def main() -> int:
    with io.open(TARGET, "r", encoding="utf-8") as f:
        src = f.read()
    for i, (old, new) in enumerate(REPLACEMENTS, 1):
        cnt = src.count(old)
        if cnt != 1:
            print(f"❌ 第 {i} 处：匹配 {cnt} 次（要求 1 次）")
            return 1
        src = src.replace(old, new)
    with io.open(TARGET + ".tmp", "w", encoding="utf-8") as f:
        f.write(src)
    os.replace(TARGET + ".tmp", TARGET)
    print(f"✅ 已应用 {len(REPLACEMENTS)} 处替换 → {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
