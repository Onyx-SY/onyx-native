# -*- coding: utf-8 -*-
"""step-1 补：修「基于 token 的智能缩进」这条死链。

实测：`MultiLineInput.lexer` 是 prompt_toolkit 的 `PygmentsLexer` 包装器，
**没有 `get_tokens`** → `_compute_smart_indent` 每次都 AttributeError，被
`except Exception: pass` 静默吞掉 → 号称「基于 Pygments token 分析」的缩进从未生效。
修法：单独持有原始 pygments lexer 实例（`raw_lexer`），语法切换时同步更新；
并补齐 bash 的块开启/结束词缩进规则。
"""
import io
import sys

P = "lib/terminal/mul_line.py"
s = io.open(P, encoding="utf-8").read()


def rep(old, new, tag):
    global s
    n = s.count(old)
    if n != 1:
        print(f"❌ {tag}: 命中 {n} 次")
        sys.exit(1)
    s = s.replace(old, new)
    print(f"✅ {tag}")


# ① __init__：新增 raw_lexer
rep('''        self.kb = self._create_key_bindings()
        self.lexer = self._get_pygments_lexer(self.current_syntax)
        self.completer = MultiLineCompleter(self.current_syntax, virtual_root)''',
    '''        self.kb = self._create_key_bindings()
        self.lexer = self._get_pygments_lexer(self.current_syntax)
        # 原始 pygments lexer（token 分析用）。self.lexer 是 ptk 的**渲染包装器**，
        # 没有 get_tokens —— 旧代码拿它做 token 分析必然 AttributeError，
        # 又被 `except Exception: pass` 吞掉，导致「基于 token 的智能缩进」从未生效。
        self.raw_lexer = self._get_raw_pygments_lexer(self.current_syntax)
        self.completer = MultiLineCompleter(self.current_syntax, virtual_root)''',
    "__init__ raw_lexer")

# ② 重写 _get_pygments_lexer 并新增 _get_raw_pygments_lexer
OLD = '''    def _get_pygments_lexer(self, syntax: str):
        _ensure_pygments_loaded()
        if not HAS_PYGMENTS:
            return None
        
        lexer_map = {
            'bash': BashLexer,
            'sh': BashLexer,
            'shell': BashLexer,
            'python': Python3Lexer,
            'python3': Python3Lexer,
            'py': Python3Lexer,
            'c': CLexer,
            'cpp': CppLexer,
            'c++': CppLexer,
            'java': JavaLexer,
            'javascript': JavaScriptLexer,
            'js': JavaScriptLexer,
            'typescript': TypeScriptLexer,
            'ts': TypeScriptLexer,
            'go': GoLexer,
            'rust': RustLexer,
            'ruby': RubyLexer,
            'perl': PerlLexer,
            'lua': LuaLexer,
            'sql': SqlLexer,
            'html': HtmlLexer,
            'css': CssLexer,
            'yaml': YamlLexer,
            'yml': YamlLexer,
            'json': JsonLexer,
            'markdown': MarkdownLexer,
            'md': MarkdownLexer,
        }
        
        lexer_class = lexer_map.get(syntax.lower())
        if lexer_class:
            return PygmentsLexer(lexer_class)
        
        try:
            return PygmentsLexer(get_lexer_by_name(syntax))
        except Exception:
            return None
'''

NEW = '''    # 语法名 → pygments lexer **类名**（运行时由 _ensure_pygments_loaded 注入到模块全局）。
    # 存名字而不是类对象，避免在模块导入期就依赖尚未注入的符号。
    _SYNTAX_LEXER_NAMES = {
        'bash': 'BashLexer', 'sh': 'BashLexer', 'shell': 'BashLexer',
        'python': 'Python3Lexer', 'python3': 'Python3Lexer', 'py': 'Python3Lexer',
        'c': 'CLexer', 'cpp': 'CppLexer', 'c++': 'CppLexer',
        'java': 'JavaLexer',
        'javascript': 'JavaScriptLexer', 'js': 'JavaScriptLexer',
        'typescript': 'TypeScriptLexer', 'ts': 'TypeScriptLexer',
        'go': 'GoLexer', 'rust': 'RustLexer', 'ruby': 'RubyLexer',
        'perl': 'PerlLexer', 'lua': 'LuaLexer', 'sql': 'SqlLexer',
        'html': 'HtmlLexer', 'css': 'CssLexer',
        'yaml': 'YamlLexer', 'yml': 'YamlLexer',
        'json': 'JsonLexer', 'markdown': 'MarkdownLexer', 'md': 'MarkdownLexer',
    }

    @staticmethod
    def _lexer_class(syntax: str):
        """语法名 → pygments lexer 类（未加载/未知语法返回 None）。"""
        name = MultiLineInput._SYNTAX_LEXER_NAMES.get((syntax or "").lower())
        return globals().get(name) if name else None

    def _get_pygments_lexer(self, syntax: str):
        """渲染用 lexer（prompt_toolkit 包装器）。"""
        _ensure_pygments_loaded()
        if not HAS_PYGMENTS:
            return None

        lexer_class = self._lexer_class(syntax)
        if lexer_class:
            return PygmentsLexer(lexer_class)

        try:
            return PygmentsLexer(get_lexer_by_name(syntax))
        except Exception:
            return None

    def _get_raw_pygments_lexer(self, syntax: str):
        """token 分析用 lexer（原始 pygments 实例，有 get_tokens）。"""
        _ensure_pygments_loaded()
        if not HAS_PYGMENTS:
            return None

        lexer_class = self._lexer_class(syntax)
        try:
            if lexer_class:
                return lexer_class()
            return get_lexer_by_name(syntax)
        except Exception:
            return None

    def _apply_syntax_lexer(self, syntax: str) -> None:
        """语法切换时同步「渲染 lexer」与「token 分析 lexer」。"""
        self.current_syntax = syntax
        self.lexer = self._get_pygments_lexer(syntax)
        self.raw_lexer = self._get_raw_pygments_lexer(syntax)
        if getattr(self, "completer", None) is not None:
            self.completer.syntax = syntax
'''
rep(OLD, NEW, "_get_pygments_lexer 重写")

# ③ 语法切换处统一走 _apply_syntax_lexer
rep('''        if state.syntax and state.syntax != self.current_syntax:
            self.current_syntax = state.syntax
            self.lexer = self._get_pygments_lexer(state.syntax)
            self.completer.syntax = state.syntax''',
    '''        if state.syntax and state.syntax != self.current_syntax:
            self._apply_syntax_lexer(state.syntax)''',
    "切换点 1")

rep('''                elif new_state is not None:
                    state = new_state
                    self.current_syntax = state.syntax
                    self.lexer = self._get_pygments_lexer(state.syntax)
                    self.completer.syntax = state.syntax''',
    '''                elif new_state is not None:
                    state = new_state
                    self._apply_syntax_lexer(state.syntax)''',
    "切换点 2")

rep('''                        if new_syntax and new_syntax != state.syntax:
                            state.syntax = new_syntax
                            state.heredoc_syntax_locked = True
                            self.current_syntax = new_syntax
                            self.lexer = self._get_pygments_lexer(new_syntax)
                            self.completer.syntax = new_syntax
                            continue''',
    '''                        if new_syntax and new_syntax != state.syntax:
                            state.syntax = new_syntax
                            state.heredoc_syntax_locked = True
                            self._apply_syntax_lexer(new_syntax)
                            continue''',
    "切换点 3")

# ④ _compute_smart_indent：用 raw_lexer + 补 bash 块词
rep('''        # 注意：本类（MultiLineInput）的属性是 self.lexer；旧代码写成 self._pygments_lexer，
        # 那是 MultiLineCompleter 的字段 → pygments 一旦恢复就 AttributeError。
        if not HAS_PYGMENTS or not self.lexer:
            return self._simple_indent_rule(current_line)''',
    '''        # 必须用 raw_lexer（原始 pygments 实例）：self.lexer 是 ptk 包装器，没有 get_tokens。
        if not HAS_PYGMENTS or not self.raw_lexer:
            return self._simple_indent_rule(current_line)''',
    "缩进 guard")

rep('''            tokens = list(self.lexer.get_tokens(current_line))''',
    '''            tokens = list(self.raw_lexer.get_tokens(current_line))''',
    "缩进 tokens")

rep('''            elif self.current_syntax in ('bash', 'sh'):
                # Bash 缩进规则：简单处理，do/then 后增加缩进
                if re.search(r'\\b(do|then)\\b\\s*$', stripped):
                    return base_indent + self.indent_width
                elif re.search(r'\\b(else|elif)\\b\\s*$', stripped):
                    return max(0, base_indent - self.indent_width)
                return base_indent''',
    '''            elif self.current_syntax in ('bash', 'sh'):
                # Bash：块开启词 +1；块结束词 / else·elif 先回退一级
                if re.search(r'\\b(fi|done|esac)\\b\\s*$', stripped) or stripped.startswith('}'):
                    return max(0, base_indent - self.indent_width)
                if re.search(r'\\b(else|elif)\\b\\s*$', stripped):
                    return max(0, base_indent - self.indent_width)
                if re.search(r'\\b(do|then)\\b\\s*$', stripped) or stripped.endswith('{'):
                    return base_indent + self.indent_width
                return base_indent''',
    "bash 缩进规则")

io.open(P, "w", encoding="utf-8").write(s)
print("written")
