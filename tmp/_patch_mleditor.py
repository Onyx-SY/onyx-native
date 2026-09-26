# -*- coding: utf-8 -*-
"""step-1：往 lib/terminal/mul_line.py 追加全屏多行编辑区 MultiLineEditor。"""
import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "lib", "terminal", "mul_line.py")

CODE = '''

# ═══════════════════════ 独立全屏多行编辑区（Alt+Enter 进入）═══════════════════════

class MultiLineEditor:
    """独立的全屏多行编辑区。

    与 `MultiLineInput`（自动续行）不同：这是一个**占满整屏的编辑器** ——
    可以上下滚动回看、能回到任意一行修改，适合写长命令 / 多行脚本。

    键位（默认；键名可在 `~/.config/onyx/ptk.json` 的 key_bindings 里改）：
      Enter                 换行
      Alt+Enter / Ctrl+D    提交（返回文本）
      Ctrl+C / Ctrl+Q       取消（返回 None）

    用法：
        text = MultiLineEditor(syntax="bash").edit(initial_text)
        if text is None:
            ...   # 用户取消，按原样处理
    """

    EDITOR_STYLE = Style.from_dict({
        "editor-title": "bg:#005f87 #ffffff bold",
        "editor-status": "bg:#303030 #cccccc",
        "editor-frame": "#5f87af",
        "textarea": "#e4e4e4",
    })

    def __init__(self, syntax: str = "bash", lang: str = "chinese", title: str = ""):
        self.syntax = syntax or "bash"
        self.lang = lang
        _cn = str(lang).lower().startswith("chi") or str(lang).lower() in ("zh", "cn")
        self.title = title or ("多行编辑区" if _cn else "Multi-line editor")
        self._cn = _cn

    def _lexer(self):
        """语法高亮 lexer（Pygments 不可用则返回 None，不影响编辑）。"""
        try:
            _ensure_pygments_loaded()
            from pygments.lexers import get_lexer_by_name
            name = {"bash": "bash", "sh": "bash", "zsh": "bash", "fish": "bash",
                    "python": "python", "c": "c", "cpp": "cpp"}.get(self.syntax, self.syntax)
            return PygmentsLexer(type(get_lexer_by_name(name)))
        except Exception:
            return None

    def edit(self, initial_text: str = "") -> Optional[str]:
        """打开编辑区；返回编辑后的文本；用户取消返回 None。"""
        try:
            from prompt_toolkit.application import Application
            from prompt_toolkit.layout import Layout, HSplit, Window
            from prompt_toolkit.layout.controls import FormattedTextControl
            from prompt_toolkit.widgets import TextArea, Frame
        except Exception:
            return initial_text          # 环境不支持 → 原样返回，不阻断主流程

        kb = KeyBindings()

        @kb.add("escape", "enter")
        @kb.add("c-d")
        def _accept(event):
            event.app.exit(result=text_area.text)

        @kb.add("c-c")
        @kb.add("c-q")
        def _cancel(event):
            event.app.exit(result=None)

        text_area = TextArea(
            text=initial_text or "",
            multiline=True,
            scrollbar=True,
            line_numbers=True,
            wrap_lines=True,
            lexer=self._lexer(),
        )

        def _status():
            doc = text_area.buffer.document
            row = doc.cursor_position_row + 1
            col = doc.cursor_position_col + 1
            if self._cn:
                hint = "Enter=换行 · Alt+Enter / Ctrl+D=提交 · Ctrl+C=取消"
            else:
                hint = "Enter=newline · Alt+Enter / Ctrl+D=submit · Ctrl+C=cancel"
            return [("class:editor-status",
                     f" {hint}   │  {row}:{col}  │  {doc.line_count} lines ")]

        body = HSplit([
            Window(FormattedTextControl([("class:editor-title", f" {self.title} ")]),
                   height=1),
            Frame(text_area, title=" Onyx "),
            Window(FormattedTextControl(_status), height=1, style="class:editor-status"),
        ])
        app = Application(
            layout=Layout(body, focused_element=text_area),
            key_bindings=kb,
            full_screen=True,
            style=self.EDITOR_STYLE,
            mouse_support=True,
        )
        try:
            return app.run()
        except (KeyboardInterrupt, EOFError):
            return None
        except Exception:
            return initial_text
'''

if "class MultiLineEditor:" in io.open(P, encoding="utf-8").read():
    print("SKIP：已存在")
else:
    with io.open(P, "a", encoding="utf-8") as f:
        f.write(CODE)
    print("OK 已追加 MultiLineEditor")
