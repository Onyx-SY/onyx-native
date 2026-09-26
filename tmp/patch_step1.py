# -*- coding: utf-8 -*-
"""step-1：救活语法高亮。
① `_ensure_pygments_loaded` 里 `JavaScriptLexer` 在 pygments≥2.20 不存在（正确名 `JavascriptLexer`），
   整个 import 块失败 → HAS_PYGMENTS=False → 多行高亮/补全/缩进全部静默关闭。
   改为**逐个容错导入**：核心必须成功，单个 lexer 缺失只跳过该语言。
② `MultiLineInput._compute_smart_indent` 引用不存在的 `self._pygments_lexer`
   （该类只有 `self.lexer`）→ pygments 一恢复就 AttributeError。改回 `self.lexer`。
"""
import io
import sys

P = "lib/terminal/mul_line.py"
s = io.open(P, encoding="utf-8").read()

OLD_LOADER = '''# pygments 延迟加载：24 个 lexer 仅在首次多行输入时才导入（节省 ~200ms 启动时间）
HAS_PYGMENTS = False
_PYGMENTS_LOADED = False

def _ensure_pygments_loaded():
    """延迟导入 pygments 并注入到模块全局命名空间（仅在首次语法高亮时触发）"""
    global HAS_PYGMENTS, _PYGMENTS_LOADED
    
    if _PYGMENTS_LOADED:
        return
    _PYGMENTS_LOADED = True
    
    try:
        from pygments.lexers import (
            BashLexer, PythonLexer, Python3Lexer, Python3TracebackLexer,
            CLexer, CppLexer, JavaLexer, JavaScriptLexer, TypeScriptLexer,
            GoLexer, RustLexer, RubyLexer, PerlLexer, LuaLexer,
            SqlLexer, HtmlLexer, CssLexer, YamlLexer, JsonLexer, MarkdownLexer,
            get_lexer_by_name, get_lexer_for_filename, guess_lexer,
        )
        from pygments.token import Token
        from pygments import highlight
        from pygments.formatters import TerminalFormatter
        
        # 注入到模块全局命名空间，让现有代码无需改动
        _globals = globals()
        _globals.update({
            'BashLexer': BashLexer, 'PythonLexer': PythonLexer,
            'Python3Lexer': Python3Lexer, 'Python3TracebackLexer': Python3TracebackLexer,
            'CLexer': CLexer, 'CppLexer': CppLexer, 'JavaLexer': JavaLexer,
            'JavaScriptLexer': JavaScriptLexer, 'TypeScriptLexer': TypeScriptLexer,
            'GoLexer': GoLexer, 'RustLexer': RustLexer, 'RubyLexer': RubyLexer,
            'PerlLexer': PerlLexer, 'LuaLexer': LuaLexer,
            'SqlLexer': SqlLexer, 'HtmlLexer': HtmlLexer, 'CssLexer': CssLexer,
            'YamlLexer': YamlLexer, 'JsonLexer': JsonLexer, 'MarkdownLexer': MarkdownLexer,
            'get_lexer_by_name': get_lexer_by_name,
            'get_lexer_for_filename': get_lexer_for_filename,
            'guess_lexer': guess_lexer,
            'Token': Token, 'highlight': highlight,
            'TerminalFormatter': TerminalFormatter,
        })
        HAS_PYGMENTS = True
    except ImportError:
        HAS_PYGMENTS = False
'''

NEW_LOADER = '''# pygments 延迟加载：lexer 仅在首次需要语法高亮时才导入（节省启动时间）
HAS_PYGMENTS = False
_PYGMENTS_LOADED = False

# 别名 → pygments 里的真实类名。
# 注意 `JavascriptLexer`（小写 s）才是 pygments 的正式名；旧代码写成 `JavaScriptLexer`
# 导致整个 import 块 ImportError → HAS_PYGMENTS 永远 False → 多行高亮/补全/缩进全灭。
# 这里改为**逐个容错导入**：核心模块必须成功，单个 lexer 改名/缺失只跳过那一种语言。
_LEXER_SPECS = {
    'BashLexer': 'BashLexer',
    'PythonLexer': 'PythonLexer',
    'Python3Lexer': 'Python3Lexer',
    'Python3TracebackLexer': 'Python3TracebackLexer',
    'CLexer': 'CLexer',
    'CppLexer': 'CppLexer',
    'JavaLexer': 'JavaLexer',
    'JavaScriptLexer': 'JavascriptLexer',
    'TypeScriptLexer': 'TypeScriptLexer',
    'GoLexer': 'GoLexer',
    'RustLexer': 'RustLexer',
    'RubyLexer': 'RubyLexer',
    'PerlLexer': 'PerlLexer',
    'LuaLexer': 'LuaLexer',
    'SqlLexer': 'SqlLexer',
    'HtmlLexer': 'HtmlLexer',
    'CssLexer': 'CssLexer',
    'YamlLexer': 'YamlLexer',
    'JsonLexer': 'JsonLexer',
    'MarkdownLexer': 'MarkdownLexer',
}
_PYGMENTS_MISSING = []   # 缺失的 lexer（诊断用，不影响其余语言）


def _ensure_pygments_loaded():
    """延迟导入 pygments 并注入模块全局命名空间（首次语法高亮时触发）。

    容错策略：核心（Token/highlight/formatter/三个查找函数）失败才算不可用；
    单个 lexer 类缺失只记入 `_PYGMENTS_MISSING`，不再拖垮整体高亮。
    """
    global HAS_PYGMENTS, _PYGMENTS_LOADED

    if _PYGMENTS_LOADED:
        return
    _PYGMENTS_LOADED = True

    try:
        import pygments.lexers as _lexers
        from pygments.token import Token
        from pygments import highlight
        from pygments.formatters import TerminalFormatter
        from pygments.lexers import get_lexer_by_name, get_lexer_for_filename, guess_lexer
    except Exception:
        HAS_PYGMENTS = False
        return

    # 注入到模块全局命名空间，让现有代码无需改动
    _globals = globals()
    _globals.update({
        'get_lexer_by_name': get_lexer_by_name,
        'get_lexer_for_filename': get_lexer_for_filename,
        'guess_lexer': guess_lexer,
        'Token': Token,
        'highlight': highlight,
        'TerminalFormatter': TerminalFormatter,
    })
    for alias, real in _LEXER_SPECS.items():
        cls = getattr(_lexers, real, None)
        if cls is None:
            _PYGMENTS_MISSING.append(f"{alias}({real})")
            continue
        _globals[alias] = cls
    HAS_PYGMENTS = True
'''

if s.count(OLD_LOADER) != 1:
    print("❌ loader 命中", s.count(OLD_LOADER))
    sys.exit(1)
s = s.replace(OLD_LOADER, NEW_LOADER)
print("✅ _ensure_pygments_loaded 改为逐个容错导入")

# ② 属性名修正
OLD_A = '''        if not HAS_PYGMENTS or not self._pygments_lexer:
            return self._simple_indent_rule(current_line)'''
NEW_A = '''        # 注意：本类（MultiLineInput）的属性是 self.lexer；旧代码写成 self._pygments_lexer，
        # 那是 MultiLineCompleter 的字段 → pygments 一旦恢复就 AttributeError。
        if not HAS_PYGMENTS or not self.lexer:
            return self._simple_indent_rule(current_line)'''
if s.count(OLD_A) != 1:
    print("❌ indent guard 命中", s.count(OLD_A))
    sys.exit(1)
s = s.replace(OLD_A, NEW_A)

OLD_B = '''            tokens = list(self._pygments_lexer.get_tokens(current_line))'''
NEW_B = '''            tokens = list(self.lexer.get_tokens(current_line))'''
if s.count(OLD_B) != 1:
    print("❌ indent tokens 命中", s.count(OLD_B))
    sys.exit(1)
s = s.replace(OLD_B, NEW_B)
print("✅ _compute_smart_indent 属性名修正")

io.open(P, "w", encoding="utf-8").write(s)
print("written")
