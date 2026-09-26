# -*- coding: utf-8 -*-
"""step-3/4/5：把 OnyxTUI 的输入区改为「单行 Input + 多行 PromptArea」双控件。

- Input：挂历史虚影 suggester，→ 直接接受虚影（Textual 原生行为）
- PromptArea：Alt+Enter 进入多行（Enter=换行 / Alt+Enter=发送），超高折叠
- 全部界面文案走 _t() 双语

用法：python3 tmp/patch_tui_app.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "bin", "ai_tui.py")

REPLACEMENTS = [
    # ── 1. CSS：底部输入区改成容器 + 两个输入控件 + 提示行 ──
    (
        "        #prompt { dock: bottom; }\n",
        "        #prompt-wrap { dock: bottom; height: auto; }\n"
        "        #prompt { width: 100%; }\n"
        "        #prompt-ml { width: 100%; display: none; }\n"
        "        #prompt-hint { height: 1; color: $text-muted; }\n",
    ),
    # ── 2. BINDINGS：Alt+Enter 进入多行模式（非 priority：多行框自己的 priority 绑定优先）──
    (
        '        BINDINGS = [Binding("ctrl+c", "quit", "Quit"), Binding("ctrl+q", "quit", "Quit"),\n'
        '                    Binding("ctrl+d", "eof_quit", "Quit", priority=True)]\n',
        '        BINDINGS = [Binding("ctrl+c", "quit", "Quit"), Binding("ctrl+q", "quit", "Quit"),\n'
        '                    Binding("ctrl+d", "eof_quit", "Quit", priority=True),\n'
        '                    Binding("alt+enter", "to_multiline", "Multiline", show=False)]\n',
    ),
    # ── 3. __init__：语言 + 虚影 suggester ──
    (
        "            self._adapter = TUIAdapter(self)\n"
        "            self._engine_kwargs = {\n",
        "            self._adapter = TUIAdapter(self)\n"
        "            self._lang = (ctx or {}).get(\"lang\", \"chinese\") or \"chinese\"\n"
        "            self._suggester = HistorySuggester(\n"
        "                _ai_history_path(self._sk.get(\"user_home_dir\") or \"\"),\n"
        "                _slash_commands(self._lang),\n"
        "            )\n"
        "            self._engine_kwargs = {\n",
    ),
    # ── 4. Ctrl+D：多行框/单行框分别处理 ──
    (
        '        def action_eof_quit(self) -> None:\n'
        '            """Ctrl+D：输入框为空 → 退出 TUI；有内容 → 删除光标右侧字符（对齐常规编辑器）。\n'
        '\n'
        '            priority=True 绑定，保证盖过 Textual Input 自带的 ctrl+d=delete_right。\n'
        '            """\n'
        '            try:\n'
        '                _inp = self.query_one("#prompt", Input)\n'
        '            except Exception:\n'
        '                _inp = None\n'
        '            if _inp is not None and _inp.value:\n'
        '                try:\n'
        '                    _inp.action_delete_right()\n'
        '                except Exception:\n'
        '                    pass\n'
        '                return\n'
        '            self.exit()\n',
        '        def action_eof_quit(self) -> None:\n'
        '            """Ctrl+D：输入框为空 → 退出 TUI；有内容 → 删除光标右侧字符（对齐常规编辑器）。\n'
        '\n'
        '            priority=True 绑定，保证盖过 Textual Input / TextArea 自带的 ctrl+d。\n'
        '            """\n'
        '            ml = inp = None\n'
        '            try:\n'
        '                ml = self.query_one("#prompt-ml", PromptArea)\n'
        '            except Exception:\n'
        '                pass\n'
        '            try:\n'
        '                inp = self.query_one("#prompt", Input)\n'
        '            except Exception:\n'
        '                pass\n'
        '            if ml is not None and ml.display and ml.text:\n'
        '                try:\n'
        '                    ml.action_delete_right()\n'
        '                except Exception:\n'
        '                    pass\n'
        '                return\n'
        '            if inp is not None and inp.display and inp.value:\n'
        '                try:\n'
        '                    inp.action_delete_right()\n'
        '                except Exception:\n'
        '                    pass\n'
        '                return\n'
        '            self.exit()\n',
    ),
    # ── 5. compose：侧栏/占位符双语 + 输入区容器 ──
    (
        '        def compose(self):\n'
        '            yield Header(show_clock=True)\n'
        '            with Horizontal(id="body"):\n'
        '                yield RichLog(id="log", wrap=True, markup=False, highlight=False)\n'
        '                with Vertical(id="sidebar"):\n'
        '                    yield Static("📋 TODO", classes="panel-title")\n'
        '                    yield Static("（暂无任务 / no tasks）", id="todo-body")\n'
        '                    yield Static("📁 FILES", classes="panel-title")\n'
        '                    yield DirectoryTree(os.getcwd(), id="files")\n'
        '            yield Input(placeholder="输入消息后回车…（AI 运行时输入将排队引导）", id="prompt")\n',
        '        def compose(self):\n'
        '            yield Header(show_clock=True)\n'
        '            with Horizontal(id="body"):\n'
        '                yield RichLog(id="log", wrap=True, markup=False, highlight=False)\n'
        '                with Vertical(id="sidebar"):\n'
        '                    yield Static(_t("tui_todo_title", self._lang), classes="panel-title")\n'
        '                    yield Static(_t("tui_tasks_none", self._lang), id="todo-body")\n'
        '                    yield Static(_t("tui_files_title", self._lang), classes="panel-title")\n'
        '                    yield DirectoryTree(os.getcwd(), id="files")\n'
        '            with Vertical(id="prompt-wrap"):\n'
        '                yield Static(_t("tui_single_hint", self._lang), id="prompt-hint")\n'
        '                yield Input(placeholder=_t("tui_placeholder", self._lang), id="prompt",\n'
        '                            suggester=self._suggester)\n'
        '                yield PromptArea(id="prompt-ml",\n'
        '                                 placeholder=_t("tui_placeholder", self._lang),\n'
        '                                 omitted_template=_t("tui_omitted", self._lang))\n',
    ),
    # ── 6. on_mount：标题/欢迎语双语 ──
    (
        '            try:\n'
        '                self.title = "Onyx AI — TUI"\n'
        '            except Exception:\n'
        '                pass\n'
        '            self._log("🤖 Onyx AI — TUI 模式。底部输入框可直接对 AI 说话；AI 运行时输入会排队，"\n'
        '                      "当前轮结束后依次处理。Ctrl+Q / Ctrl+D 退出。")\n',
        '            try:\n'
        '                self.title = _t("tui_title", self._lang)\n'
        '            except Exception:\n'
        '                pass\n'
        '            self._log(_t("tui_welcome", self._lang))\n',
    ),
    # ── 7. 输入：统一提交 + 单行/多行切换 ──
    (
        '        # ── 输入 ──\n'
        '        def on_input_submitted(self, event):\n'
        '            text = (event.value or "").strip()\n'
        '            try:\n'
        '                self.query_one("#prompt", Input).value = ""\n'
        '            except Exception:\n'
        '                pass\n'
        '            if not text:\n'
        '                return\n'
        '            self._in_q.put(text)\n'
        '            if self._busy:\n'
        '                self._log(f"⏳ 已排队（当前轮结束后处理）：{text}")\n',
        '        # ── 输入 ──\n'
        '        def _set_hint(self, key: str) -> None:\n'
        '            try:\n'
        '                self.query_one("#prompt-hint", Static).update(_t(key, self._lang))\n'
        '            except Exception:\n'
        '                pass\n'
        '\n'
        '        def _submit(self, text: str) -> None:\n'
        '            """统一提交入口：文本进队列（AI 忙时排队 = 实时引导）。"""\n'
        '            text = (text or "").strip()\n'
        '            if not text:\n'
        '                return\n'
        '            self._in_q.put(text)\n'
        '            if self._busy:\n'
        '                self._log(_t("tui_queued", self._lang, text=text))\n'
        '\n'
        '        def on_input_submitted(self, event):\n'
        '            """单行框：Enter 发送（虚影由 → 接受）。"""\n'
        '            try:\n'
        '                self.query_one("#prompt", Input).value = ""\n'
        '            except Exception:\n'
        '                pass\n'
        '            self._submit(event.value)\n'
        '\n'
        '        # ── 多行模式：Alt+Enter 进入；多行框内 Enter=换行、Alt+Enter=发送 ──\n'
        '        def action_to_multiline(self) -> None:\n'
        '            """单行框按 Alt+Enter：切到多行框，并把已输入内容带过去。"""\n'
        '            try:\n'
        '                inp = self.query_one("#prompt", Input)\n'
        '                ml = self.query_one("#prompt-ml", PromptArea)\n'
        '            except Exception:\n'
        '                return\n'
        '            if ml.display:\n'
        '                return\n'
        '            text = inp.value or ""\n'
        '            ml.text = text + "\\n" if text else ""\n'
        '            try:\n'
        '                ml.cursor_location = ml.document.end\n'
        '            except Exception:\n'
        '                pass\n'
        '            inp.value = ""\n'
        '            inp.display = False\n'
        '            ml.display = True\n'
        '            ml.focus()\n'
        '            self._set_hint("tui_ml_hint")\n'
        '\n'
        '        def _exit_multiline(self) -> None:\n'
        '            try:\n'
        '                inp = self.query_one("#prompt", Input)\n'
        '                ml = self.query_one("#prompt-ml", PromptArea)\n'
        '            except Exception:\n'
        '                return\n'
        '            ml.text = ""\n'
        '            ml.display = False\n'
        '            inp.display = True\n'
        '            inp.value = ""\n'
        '            inp.focus()\n'
        '            self._set_hint("tui_single_hint")\n'
        '\n'
        '        def on_prompt_area_submitted(self, event) -> None:\n'
        '            """多行框 Alt+Enter：整体发送并退回单行框。"""\n'
        '            text = event.text\n'
        '            self._exit_multiline()\n'
        '            self._submit(text)\n',
    ),
]


def main() -> int:
    with io.open(TARGET, "r", encoding="utf-8") as f:
        src = f.read()

    for i, (old, new) in enumerate(REPLACEMENTS, 1):
        cnt = src.count(old)
        if cnt != 1:
            print(f"❌ 第 {i} 处替换：匹配 {cnt} 次（要求恰好 1 次）")
            return 1
        src = src.replace(old, new)

    tmp = TARGET + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(src)
    os.replace(tmp, TARGET)
    print(f"✅ 已应用 {len(REPLACEMENTS)} 处替换 → {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
