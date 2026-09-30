# lib/terminal/mul_line.py
"""
多行输入模块 - 增强版
支持 here document、if/fi、for/do/done 等多行命令输入
使用 Pygments 进行语法高亮（支持 Python、Bash、C 等多种语法）
新增：支持 here-document 中通过 #语法 动态切换语法（如 #python、#bash、#c 等）
新增：多行命令格式化显示（历史导航时正确换行）
新增：CMD 多行命令支持（IF/FOR 块结构）
新增：heredoc 未安装 Pygments 时使用主 lexer 替代
修复：嵌套多行结构误判，增加深度栈管理
修复：语法切换时补全器和高亮器正确更新
修复：智能缩进：基于 Pygments token 分析实现正确的自动缩进
修复：多行补全增强，利用上下文感知提供更精准的关键字和路径补全
修复（AST 语法检查专项）：
- validate_python 拆分「严格校验」与「允许修复」两种语义，杜绝不完整代码被误判为有效
- _fix_incomplete_code 补括号时感知字符串/注释，避免误补或漏补
- _is_brackets_closed / _is_parens_closed 区分「正常闭合」与「错误闭合」
- validate_bash 在无 bash 可执行文件时不再误报语法错误
- TERMINATORS 中 python_* 的终止条件更贴近实际缩进块语义
- _complete_path 异常分支补上 yield from
- SmartSyntaxDetector.detect 无任何特征命中时回退 BASH，而非误判 PYTHON
- _ensure_pygments_loaded 加线程锁，避免竞态半初始化

修复（续行检测缺口专项 · P2）：
- MultiLineDetector 新增 _has_unclosed_quote / _has_unclosed_paren 两个
  引号/括号感知的静态检查（跳过转义、引号内部不当成配对）
- detect() 在所有既定模式未命中时新增兜底：
    · 未闭合引号   → 返回 unclosed_quote 状态（新类型）
    · 未闭合括号   → 复用 bracket_open 状态（含行中括号）
  此前 echo "abc、print(1 这类「明显没打完」的输入识别不出，
  直接当完整命令扔给 shell 报语法错误
- TERMINATORS 新增 unclosed_quote 终止条件
- CONTINUATION_PROMPTS / _simple_multiline_input.prompts 补充
  'unclosed_quote': 'quote> ' 提示符
- _simple_multiline_input 补充 state.lines 累积（旧路径此前不累积，
  导致基于 state.lines 的终止条件永远只看到当前行）
"""

import re
import os
import sys
import ast
import subprocess
import tempfile
import threading
from typing import Optional, List, Tuple, Dict, Any, Callable, Set, Union
from dataclasses import dataclass, field
from pathlib import Path
from enum import Enum

from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.lexers import PygmentsLexer
from prompt_toolkit.styles import Style
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document

# 新增：导入终端类型检测
try:
    from lib.get_terminal_type import get_terminal_type as _get_terminal_type
except ImportError:
    try:
        from ..get_terminal_type import get_terminal_type as _get_terminal_type  # type: ignore
    except (ImportError, ValueError):
        def _get_terminal_type():
            return 'sh'

# pygments 延迟加载：24 个 lexer 仅在首次多行输入时才导入（节省 ~200ms 启动时间）
HAS_PYGMENTS = False
_PYGMENTS_LOADED = False
_PYGMENTS_LOCK = threading.RLock()

def _ensure_pygments_loaded():
    """延迟导入 pygments 并注入到模块全局命名空间（仅在首次语法高亮时触发）

    修复：加锁保证线程安全；_PYGMENTS_LOADED 仅在成功导入后才置 True，
    失败时允许后续重试。
    """
    global HAS_PYGMENTS, _PYGMENTS_LOADED

    if _PYGMENTS_LOADED:
        return
    with _PYGMENTS_LOCK:
        if _PYGMENTS_LOADED:
            return
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
            _PYGMENTS_LOADED = True
        except ImportError:
            HAS_PYGMENTS = False
            # 允许后续重试（虽然 ImportError 通常不会自愈，但保证状态一致）
            _PYGMENTS_LOADED = False


# ===================== 语法类型枚举 =====================
class SyntaxType(Enum):
    BASH = "bash"
    PYTHON = "python"
    PYTHON3 = "python3"
    C = "c"
    CPP = "cpp"
    JAVA = "java"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    GO = "go"
    RUST = "rust"
    RUBY = "ruby"
    PERL = "perl"
    LUA = "lua"
    SQL = "sql"
    HTML = "html"
    CSS = "css"
    YAML = "yaml"
    JSON = "json"
    MARKDOWN = "markdown"
    UNKNOWN = "unknown"
    CMD = "cmd"

    @classmethod
    def from_string(cls, name: str) -> "SyntaxType":
        name_lower = name.lower()
        for st in cls:
            if st.value == name_lower:
                return st
        return cls.UNKNOWN


# ===================== 智能语法检测器（基于内容特征）======================
class SmartSyntaxDetector:
    PYTHON_PATTERNS = {
        'decorator': r'^@\w+(?:\.\w+)*(?:\([^)]*\))?\s*$',
        'async_def': r'^async\s+def\s+\w+\s*\([^)]*\)\s*:',
        'async_for': r'^async\s+for\s+\w+\s+in\s+',
        'async_with': r'^async\s+with\s+',
        'await': r'\bawait\s+\w+',
        'print_function': r'\bprint\s*\(',
        'f_string': r'f["\'].*?\{.*?\}.*?["\']',
        'list_comprehension': r'\[.*?for.*?in.*?\]',
        'dict_comprehension': r'\{.*?:.*?for.*?in.*?\}',
        'with_statement': r'^with\s+\w+\s+as\s+\w+\s*:',
        'except_as': r'^except\s+.*?\s+as\s+\w+\s*:',
        'type_hint': r':\s*(?:int|str|float|bool|list|dict|tuple|Optional|Union)',
        'walrus_operator': r':=',
        'match_case': r'^match\s+\w+\s*:\s*$|^case\s+\w+\s*:',
    }

    BASH_PATTERNS = {
        'variable': r'\$\{?[A-Za-z_][A-Za-z0-9_]*\}?',
        'command_substitution': r'\$\(.*?\)',
        'arithmetic': r'\$\(\(.*?\)\)',
        'if_fi': r'\bif\b.*?\bthen\b|\bfi\b',
        'for_do': r'\bfor\b.*?\bdo\b|\bdone\b',
        'while_do': r'\bwhile\b.*?\bdo\b',
        'case_esac': r'\bcase\b.*?\bin\b|\besac\b',
        'function_def': r'^\s*function\s+\w+\s*\{|^\s*\w+\(\)\s*\{',
        'redirect': r'[<>]&?\d*|&>[>&]?|<<<|<<',
        'pipe': r'\|[|&]?',
        'test_bracket': r'\[\s+.*?\s+\]|\[\[.*?\]\]',
        'here_doc': r'<<[-]?\s*[\'\"]?\w+[\'\"]?',
    }

    JS_PATTERNS = {
        'arrow_function': r'=>\s*\{?',
        'const_let': r'\b(const|let)\s+\w+\s*=',
        'template_literal': r'`.*?\${.*?}.*?`',
        'async_await': r'\basync\s+function|\basync\s*\(|await\s+',
        'promise': r'\.then\(|\.catch\(|\.finally\(',
        'class_def': r'^class\s+\w+\s*\{',
        'export_import': r'\b(export|import)\s+.*?\s+from\s+',
        'spread_operator': r'\.\.\.\w+',
        'destructuring': r'\{.*?\}\s*=|\{.*?\}\s*:',
        'optional_chaining': r'\?\.',
        'nullish_coalescing': r'\?\?',
    }

    C_PATTERNS = {
        'include': r'^#include\s+[<"]',
        'define': r'^#define\s+\w+',
        'pointer': r'\*\s*\w+|\w+\s*\*',
        'struct': r'^struct\s+\w+\s*\{',
        'typedef': r'^typedef\s+',
        'main_function': r'int\s+main\s*\(.*?\)',
        'printf_scanf': r'\b(printf|scanf|fprintf|sprintf)\s*\(',
        'memory_management': r'\b(malloc|calloc|free|realloc)\s*\(',
    }

    SQL_PATTERNS = {
        'select': r'^\s*SELECT\s+.+?\s+FROM\s+',
        'join': r'\b(INNER|LEFT|RIGHT|FULL|CROSS)?\s*JOIN\s+',
        'where': r'\bWHERE\s+\w+\s*[=<>!]+\s*',
        'group_by': r'\bGROUP\s+BY\s+',
        'order_by': r'\bORDER\s+BY\s+',
        'insert': r'^\s*INSERT\s+INTO\s+',
        'update': r'^\s*UPDATE\s+\w+\s+SET\s+',
        'delete': r'^\s*DELETE\s+FROM\s+',
        'create': r'^\s*CREATE\s+(TABLE|DATABASE|INDEX|VIEW)\s+',
        'alter': r'^\s*ALTER\s+TABLE\s+',
    }

    RUBY_PATTERNS = {
        'puts': r'\bputs\s+',
        'def_end': r'^def\s+\w+|^end$',
        'attr_accessor': r'\battr_(accessor|reader|writer)\s+',
        'symbol': r':\w+',
        'block': r'\bdo\s*\|.*?\|\s*$|\{\s*\|.*?\|\s*\}',
        'require': r'^require(\s+|_relative)\s+[\'"]',
        'gem': r'^gem\s+[\'"]',
    }

    GO_PATTERNS = {
        'package': r'^package\s+\w+',
        'func': r'^func\s+\w+\s*\(',
        'go_routine': r'\bgo\s+\w+\(',
        'channel': r'<-\s*\w+|\w+\s*<-',
        'defer': r'\bdefer\s+',
        'interface': r'^type\s+\w+\s+interface\s*\{',
    }

    MARKDOWN_PATTERNS = {
        'heading': r'^#{1,6}\s+\w+',
        'bold': r'\*\*.*?\*\*|__.*?__',
        'italic': r'\*.*?\*|_.*?_',
        'code_block': r'^```\w*$',
        'list': r'^[-*+]\s+|\d+\.\s+',
        'link': r'\[.*?\]\(.*?\)',
        'image': r'!\[.*?\]\(.*?\)',
    }

    FEATURE_WEIGHTS = {
        'python': 1.0,
        'bash': 1.0,
        'javascript': 1.0,
        'c': 1.0,
        'sql': 1.0,
        'ruby': 0.8,
        'go': 0.8,
        'markdown': 0.6,
    }

    @classmethod
    def detect(cls, content: str, context: Optional[str] = None) -> SyntaxType:
        if not content or not content.strip():
            return SyntaxType.BASH

        scores = {
            'python': 0,
            'bash': 0,
            'javascript': 0,
            'c': 0,
            'sql': 0,
            'ruby': 0,
            'go': 0,
            'markdown': 0,
        }

        lines = content.split('\n')
        content_lower = content.lower()

        for pattern_name, pattern in cls.PYTHON_PATTERNS.items():
            if re.search(pattern, content, re.MULTILINE | re.IGNORECASE):
                scores['python'] += 1
                if pattern_name in ('decorator', 'async_def', 'match_case'):
                    scores['python'] += 1

        if cls._has_consistent_indentation(lines):
            scores['python'] += 1

        if re.search(r'^(import|from)\s+\w+', content, re.MULTILINE):
            scores['python'] += 2

        for pattern_name, pattern in cls.BASH_PATTERNS.items():
            if re.search(pattern, content, re.MULTILINE | re.IGNORECASE):
                scores['bash'] += 1

        if re.search(r'\$\{?[A-Z_][A-Z0-9_]*\}?', content):
            scores['bash'] += 1

        for pattern_name, pattern in cls.JS_PATTERNS.items():
            if re.search(pattern, content, re.MULTILINE | re.IGNORECASE):
                scores['javascript'] += 1

        if re.search(r';$', content, re.MULTILINE):
            scores['javascript'] += 0.5

        for pattern_name, pattern in cls.C_PATTERNS.items():
            if re.search(pattern, content, re.MULTILINE | re.IGNORECASE):
                scores['c'] += 1

        for pattern_name, pattern in cls.SQL_PATTERNS.items():
            if re.search(pattern, content, re.MULTILINE | re.IGNORECASE):
                scores['sql'] += 2

        for pattern_name, pattern in cls.RUBY_PATTERNS.items():
            if re.search(pattern, content, re.MULTILINE | re.IGNORECASE):
                scores['ruby'] += 1

        for pattern_name, pattern in cls.GO_PATTERNS.items():
            if re.search(pattern, content, re.MULTILINE | re.IGNORECASE):
                scores['go'] += 1

        if len(lines) > 2 and not re.search(r'[;{}]', content):
            for pattern_name, pattern in cls.MARKDOWN_PATTERNS.items():
                if re.search(pattern, content, re.MULTILINE):
                    scores['markdown'] += 1

            if scores['markdown'] > 3 and max(scores['python'], scores['bash'], scores['javascript']) < 2:
                scores['markdown'] += 3

        if scores['python'] > 0 and scores['bash'] > 2:
            if re.search(r'\$\(|\$\{', content):
                scores['python'] -= 1

        for lang in scores:
            scores[lang] *= cls.FEATURE_WEIGHTS.get(lang, 1.0)

        # 修复：无任何特征命中时，直接回退 BASH，避免 max() 按字典序返回 'python'
        best_score = max(scores.values()) if scores else 0.0
        if best_score <= 0.0:
            if context:
                if context in ('python', 'python3', 'py'):
                    return SyntaxType.PYTHON
                elif context in ('node', 'js', 'javascript'):
                    return SyntaxType.JAVASCRIPT
                elif context in ('gcc', 'g++', 'c', 'cpp'):
                    return SyntaxType.C
            return SyntaxType.BASH

        best_lang = max(scores, key=scores.get)

        if best_score < 1.0 and context:
            if context in ('python', 'python3', 'py'):
                return SyntaxType.PYTHON
            elif context in ('node', 'js', 'javascript'):
                return SyntaxType.JAVASCRIPT
            elif context in ('gcc', 'g++', 'c', 'cpp'):
                return SyntaxType.C

        syntax_map = {
            'python': SyntaxType.PYTHON,
            'bash': SyntaxType.BASH,
            'javascript': SyntaxType.JAVASCRIPT,
            'c': SyntaxType.C,
            'sql': SyntaxType.SQL,
            'ruby': SyntaxType.RUBY,
            'go': SyntaxType.GO,
            'markdown': SyntaxType.MARKDOWN,
        }

        return syntax_map.get(best_lang, SyntaxType.BASH)

    @classmethod
    def _has_consistent_indentation(cls, lines: List[str]) -> bool:
        indent_levels = []
        for line in lines:
            if line.strip():
                indent = len(line) - len(line.lstrip())
                if indent > 0:
                    indent_levels.append(indent)

        if len(indent_levels) > 2:
            common_indents = [4, 2, 1]
            for ci in common_indents:
                if all(level % ci == 0 for level in indent_levels):
                    return True
        return False

    @classmethod
    def detect_from_command(cls, command: str) -> SyntaxType:
        if not command:
            return SyntaxType.UNKNOWN

        first_word = command.split()[0].lower() if command.split() else ""

        cmd_map = {
            'python': SyntaxType.PYTHON,
            'python3': SyntaxType.PYTHON,
            'py': SyntaxType.PYTHON,
            'ipython': SyntaxType.PYTHON,
            'node': SyntaxType.JAVASCRIPT,
            'npm': SyntaxType.JAVASCRIPT,
            'npx': SyntaxType.JAVASCRIPT,
            'gcc': SyntaxType.C,
            'g++': SyntaxType.CPP,
            'clang': SyntaxType.C,
            'go': SyntaxType.GO,
            'rustc': SyntaxType.RUST,
            'ruby': SyntaxType.RUBY,
            'perl': SyntaxType.PERL,
            'lua': SyntaxType.LUA,
            'mysql': SyntaxType.SQL,
            'psql': SyntaxType.SQL,
            'sqlite3': SyntaxType.SQL,
        }

        if first_word in cmd_map:
            return cmd_map[first_word]

        for ext, syntax in cls._get_extension_map().items():
            if command.endswith(ext):
                return syntax

        return SyntaxType.UNKNOWN

    @staticmethod
    def _get_extension_map() -> Dict[str, SyntaxType]:
        return {
            '.py': SyntaxType.PYTHON,
            '.js': SyntaxType.JAVASCRIPT,
            '.ts': SyntaxType.TYPESCRIPT,
            '.sh': SyntaxType.BASH,
            '.bash': SyntaxType.BASH,
            '.c': SyntaxType.C,
            '.cpp': SyntaxType.CPP,
            '.cc': SyntaxType.CPP,
            '.go': SyntaxType.GO,
            '.rs': SyntaxType.RUST,
            '.rb': SyntaxType.RUBY,
            '.sql': SyntaxType.SQL,
            '.html': SyntaxType.HTML,
            '.css': SyntaxType.CSS,
            '.json': SyntaxType.JSON,
            '.yaml': SyntaxType.YAML,
            '.yml': SyntaxType.YAML,
            '.md': SyntaxType.MARKDOWN,
        }

    @classmethod
    def detect_with_confidence(cls, content: str) -> Tuple[SyntaxType, float]:
        if not content or not content.strip():
            return SyntaxType.BASH, 0.0

        detected = cls.detect(content)

        if detected == SyntaxType.PYTHON:
            features = sum(1 for pattern in cls.PYTHON_PATTERNS.values()
                          if re.search(pattern, content, re.MULTILINE | re.IGNORECASE))
            confidence = min(0.95, features / max(1, len(cls.PYTHON_PATTERNS)) * 1.5)
        elif detected == SyntaxType.BASH:
            features = sum(1 for pattern in cls.BASH_PATTERNS.values()
                          if re.search(pattern, content, re.MULTILINE | re.IGNORECASE))
            confidence = min(0.95, features / max(1, len(cls.BASH_PATTERNS)) * 1.5)
        elif detected == SyntaxType.JAVASCRIPT:
            features = sum(1 for pattern in cls.JS_PATTERNS.values()
                          if re.search(pattern, content, re.MULTILINE | re.IGNORECASE))
            confidence = min(0.95, features / max(1, len(cls.JS_PATTERNS)) * 1.5)
        else:
            confidence = 0.5

        return detected, confidence


SyntaxDetector = SmartSyntaxDetector


# ===================== AST 语法检查器（增强版）=====================
class ASTValidator:
    """
    Python 语法检查拆分为两种语义（修复核心）：
    - validate_python(code, allow_repair=False)：
        * 严格模式（默认）用于「判断语法是否正确」 —— 只用 ast.parse，绝不自动补全
        * 宽松模式（allow_repair=True）用于「判断是否还需要继续输入」—— 允许先修复再解析
    - is_complete_python(code)：返回代码是否已是一段完整可提交的 Python 片段
    """

    @staticmethod
    def validate_python(code: str, allow_repair: bool = False) -> Tuple[bool, Optional[str], Optional[int]]:
        if not code or not code.strip():
            return True, None, None

        # 修复：严格模式只用 ast.parse，不允许自动补全掩盖语法错误
        if not allow_repair:
            try:
                ast.parse(code)
                return True, None, None
            except SyntaxError as e:
                error_msg = str(e)
                line_num = getattr(e, 'lineno', None)
                # 特殊：unexpected EOF 视为「不完整」而非「语法错误」
                if "unexpected EOF" in error_msg or "was never closed" in error_msg:
                    return False, "代码不完整，请继续输入", line_num
                friendly_msg = ASTValidator._get_friendly_python_error(error_msg)
                return False, friendly_msg, line_num
            except Exception as e:
                return False, str(e), None

        # 宽松模式：允许先修复补全
        strategies = [
            lambda c: ast.parse(c),
            lambda c: ast.parse(ASTValidator._fix_incomplete_code(c)),
            lambda c: compile(c, '<string>', 'exec', ast.PyCF_ONLY_AST),
        ]

        last_error = None
        for strategy in strategies:
            try:
                tree = strategy(code)
                ASTValidator._validate_ast_structure(tree)
                return True, None, None
            except SyntaxError as e:
                last_error = e
                if e.msg == "unexpected EOF while parsing":
                    return False, "代码不完整，请继续输入", e.lineno
            except Exception as e:
                last_error = e

        if last_error:
            error_msg = str(last_error)
            line_num = getattr(last_error, 'lineno', None)
            friendly_msg = ASTValidator._get_friendly_python_error(error_msg)
            return False, friendly_msg, line_num

        return True, None, None

    @staticmethod
    def is_complete_python(code: str) -> bool:
        """判断 Python 代码是否是完整可提交的（严格模式）"""
        if not code or not code.strip():
            return True
        try:
            ast.parse(code)
            return True
        except SyntaxError:
            return False
        except Exception:
            return False

    @staticmethod
    def _fix_incomplete_code(code: str) -> str:
        """尝试把不完整的 Python 代码补齐成可解析的形式。

        修复：扫描括号时跳过字符串、注释内的内容，避免误补/漏补。
        """
        stripped = code.strip()

        # 补齐未写完的块（def/class/if...: 后无内容）
        if re.search(r'def\s+\w+\s*\([^)]*\)\s*(?:->\s*[^:]+)?\s*:\s*$', stripped, re.MULTILINE):
            code += '\n    pass'
        elif re.search(r'class\s+\w+(?:\([^)]*\))?\s*:\s*$', stripped, re.MULTILINE):
            code += '\n    pass'
        elif re.search(r'(^|\n)\s*(if|elif|else|for|while|try|except|finally|with)\b[^\n]*:\s*$', stripped, re.MULTILINE):
            code += '\n    pass'

        # 修复：字符串/注释感知的括号扫描
        stack = []
        n = len(code)
        i = 0
        in_single = False
        in_double = False
        in_triple_single = False
        in_triple_double = False
        while i < n:
            ch = code[i]
            # 处理三引号
            if not in_single and not in_double:
                if code[i:i+3] == '"""' and not in_triple_single:
                    in_triple_double = not in_triple_double
                    i += 3
                    continue
                if code[i:i+3] == "'''" and not in_triple_double:
                    in_triple_single = not in_triple_single
                    i += 3
                    continue
            if in_triple_single or in_triple_double:
                i += 1
                continue
            # 单/双引号
            if ch == "'" and not in_double:
                in_single = not in_single
                i += 1
                continue
            if ch == '"' and not in_single:
                in_double = not in_double
                i += 1
                continue
            if in_single or in_double:
                # 转义
                if ch == '\\' and i + 1 < n:
                    i += 2
                    continue
                i += 1
                continue
            # 注释
            if ch == '#':
                j = code.find('\n', i)
                if j == -1:
                    break
                i = j + 1
                continue
            # 括号
            if ch in '([{':
                stack.append((ch, i))
            elif ch in ')]}':
                if stack and ((ch == ')' and stack[-1][0] == '(') or
                              (ch == ']' and stack[-1][0] == '[') or
                              (ch == '}' and stack[-1][0] == '{')):
                    stack.pop()
            i += 1

        for char, _ in reversed(stack):
            closing = {'(': ')', '[': ']', '{': '}'}[char]
            code += closing

        return code

    @staticmethod
    def _validate_ast_structure(tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not node.body:
                    raise SyntaxError(f"Empty {type(node).__name__} body")
            elif isinstance(node, (ast.If, ast.For, ast.While, ast.With)):
                if not node.body:
                    raise SyntaxError(f"Empty {type(node).__name__} body")

    @staticmethod
    def _get_friendly_python_error(error_msg: str) -> str:
        friendly_messages = {
            "unexpected EOF while parsing": "代码不完整，请继续输入",
            "invalid syntax": "语法错误，请检查代码",
            "indentation": "缩进错误，请检查空格和Tab",
            "unmatched": "括号不匹配",
            "invalid character": "包含无效字符",
            "unexpected character after line continuation": "行续符 \\ 后出现意外字符",
            "EOF while scanning": "字符串或括号未闭合",
            "EOL while scanning": "字符串未闭合（行尾）",
        }

        for key, msg in friendly_messages.items():
            if key in error_msg.lower():
                return msg

        return error_msg

    @staticmethod
    def _is_brackets_closed(line: str, lines: List[str]) -> bool:
        """判断累积代码的括号是否「完整、正确闭合」。

        修复：区分三种情况
        - 正常闭合（stack 空且无多余闭合符）→ True
        - 仍开着的括号（stack 非空）→ False（需要继续输入）
        - 错误闭合（多余闭合符 / 括号类型不匹配）→ False（也应继续输入让用户修正）
        同时跳过字符串、注释内的括号。
        """
        full_code = '\n'.join(lines + [line]) if lines else line

        stack = []
        n = len(full_code)
        i = 0
        in_single = False
        in_double = False
        in_triple_single = False
        in_triple_double = False
        while i < n:
            ch = full_code[i]
            if not in_single and not in_double:
                if full_code[i:i+3] == '"""' and not in_triple_single:
                    in_triple_double = not in_triple_double
                    i += 3
                    continue
                if full_code[i:i+3] == "'''" and not in_triple_double:
                    in_triple_single = not in_triple_single
                    i += 3
                    continue
            if in_triple_single or in_triple_double:
                i += 1
                continue
            if ch == "'" and not in_double:
                in_single = not in_single
                i += 1
                continue
            if ch == '"' and not in_single:
                in_double = not in_double
                i += 1
                continue
            if in_single or in_double:
                if ch == '\\' and i + 1 < n:
                    i += 2
                    continue
                i += 1
                continue
            if ch == '#':
                j = full_code.find('\n', i)
                if j == -1:
                    break
                i = j + 1
                continue
            if ch in '([{':
                stack.append(ch)
            elif ch in ')]}':
                if not stack:
                    # 多余闭合符 → 错误闭合，需继续输入修正
                    return False
                last = stack.pop()
                if (ch == ')' and last != '(') or \
                   (ch == ']' and last != '[') or \
                   (ch == '}' and last != '{'):
                    return False
            i += 1

        return len(stack) == 0

    @staticmethod
    def _is_parens_closed(line: str, lines: List[str]) -> bool:
        """判断累积代码的圆括号是否「完整、正确闭合」。

        修复：与 _is_brackets_closed 一致，错误闭合返回 False；跳过字符串/注释。
        """
        full_code = '\n'.join(lines + [line]) if lines else line

        depth = 0
        n = len(full_code)
        i = 0
        in_single = False
        in_double = False
        while i < n:
            ch = full_code[i]
            if in_single:
                if ch == '\\' and i + 1 < n:
                    i += 2
                    continue
                if ch == "'":
                    in_single = False
                i += 1
                continue
            if in_double:
                if ch == '\\' and i + 1 < n:
                    i += 2
                    continue
                if ch == '"':
                    in_double = False
                i += 1
                continue
            if ch == "'":
                in_single = True
                i += 1
                continue
            if ch == '"':
                in_double = True
                i += 1
                continue
            if ch == '#':
                j = full_code.find('\n', i)
                if j == -1:
                    break
                i = j + 1
                continue
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth < 0:
                    return False
            i += 1

        return depth == 0

    @staticmethod
    def validate_bash(code: str) -> Tuple[bool, Optional[str], Optional[int]]:
        if not code or not code.strip():
            return True, None, None

        with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False,
                                         encoding='utf-8') as f:
            f.write(code)
            temp_file = f.name

        try:
            result = subprocess.run(
                ['bash', '-n', temp_file],
                capture_output=True,
                text=True,
                timeout=2
            )
            if result.returncode == 0:
                return True, None, None
            else:
                error_msg = result.stderr.strip()
                line_match = re.search(r'line (\d+)', error_msg)
                line_num = int(line_match.group(1)) if line_match else None
                return False, error_msg, line_num
        except subprocess.TimeoutExpired:
            return False, "语法检查超时", None
        except FileNotFoundError:
            # 修复：无 bash（如纯 Windows）不误报语法错误
            return True, None, None
        except Exception as e:
            return False, str(e), None
        finally:
            try:
                os.unlink(temp_file)
            except:
                pass

    @staticmethod
    def validate_c(code: str) -> Tuple[bool, Optional[str], Optional[int]]:
        if not code or not code.strip():
            return True, None, None

        with tempfile.NamedTemporaryFile(mode='w', suffix='.c', delete=False,
                                         encoding='utf-8') as f:
            f.write(code)
            temp_file = f.name

        try:
            result = subprocess.run(
                ['gcc', '-fsyntax-only', temp_file],
                capture_output=True,
                text=True,
                timeout=2
            )
            if result.returncode == 0:
                return True, None, None
            else:
                error_msg = result.stderr.strip()
                line_match = re.search(r':(\d+):', error_msg)
                line_num = int(line_match.group(1)) if line_match else None
                return False, error_msg, line_num
        except subprocess.TimeoutExpired:
            return False, "语法检查超时", None
        except FileNotFoundError:
            return True, None, None
        except Exception as e:
            return False, str(e), None
        finally:
            try:
                os.unlink(temp_file)
            except:
                pass

    @staticmethod
    def validate_js(code: str) -> Tuple[bool, Optional[str], Optional[int]]:
        if not code or not code.strip():
            return True, None, None

        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False,
                                             encoding='utf-8') as f:
                f.write(code)
                temp_file = f.name

            result = subprocess.run(
                ['node', '--check', temp_file],
                capture_output=True,
                text=True,
                timeout=2
            )
            if result.returncode == 0:
                return True, None, None
            else:
                error_msg = result.stderr.strip()
                line_match = re.search(r':(\d+)$', error_msg.split('\n')[0] if '\n' in error_msg else error_msg)
                line_num = int(line_match.group(1)) if line_match else None
                return False, error_msg, line_num
        except FileNotFoundError:
            return True, None, None
        except Exception as e:
            return False, str(e), None
        finally:
            try:
                os.unlink(temp_file)
            except:
                pass

    @staticmethod
    def validate(code: str, syntax: str) -> Tuple[bool, Optional[str], Optional[int]]:
        syntax_lower = syntax.lower()

        if syntax_lower in ('python', 'python3', 'py'):
            return ASTValidator.validate_python(code)
        elif syntax_lower in ('bash', 'sh', 'shell'):
            return ASTValidator.validate_bash(code)
        elif syntax_lower in ('c', 'c99', 'c11'):
            return ASTValidator.validate_c(code)
        elif syntax_lower in ('javascript', 'js', 'node'):
            return ASTValidator.validate_js(code)
        elif syntax_lower in ('cmd', 'batch'):
            return True, None, None

        return True, None, None


# ===================== 多行命令格式化器 =====================
class MultiLineFormatter:

    BASH_INDENT_KEYWORDS = {
        'if': 'fi',
        'then': '',
        'else': '',
        'elif': '',
        'for': 'done',
        'while': 'done',
        'until': 'done',
        'case': 'esac',
        'do': 'done',
    }

    INDENT_WIDTH = 4

    @classmethod
    def format_multiline_command(cls, command: str) -> str:
        if not command:
            return command

        if cls._is_bash_multiline(command):
            return cls._format_bash_multiline(command)

        if cls._is_python_multiline(command):
            return cls._format_python_multiline(command)

        if cls._is_cmd_multiline(command):
            return cls._format_cmd_multiline(command)

        return command

    @classmethod
    def _is_bash_multiline(cls, command: str) -> bool:
        keywords = ['if', 'for', 'while', 'until', 'case', 'then', 'else', 'elif', 'do', 'done', 'fi', 'esac', 'function']
        first_line = command.strip().split('\n')[0].strip()
        first_word = first_line.split()[0] if first_line.split() else ''

        if first_word.lower() in keywords:
            return True

        for kw in keywords:
            if kw in command.lower():
                return True

        return False

    @classmethod
    def _is_python_multiline(cls, command: str) -> bool:
        python_keywords = ['def ', 'class ', 'if ', 'for ', 'while ', 'with ', 'try:', 'except', 'elif ', 'else:']
        first_line = command.strip().split('\n')[0].strip()

        for kw in python_keywords:
            if first_line.startswith(kw) or kw in first_line:
                return True

        return False

    @classmethod
    def _is_cmd_multiline(cls, command: str) -> bool:
        first_line = command.strip().split('\n')[0].strip().lower()
        cmd_keywords = ['if ', 'for ', 'else', 'do ']
        for kw in cmd_keywords:
            if first_line.startswith(kw) or kw in first_line:
                return True
        if command.count('(') > 0 and command.count(')') < command.count('('):
            return True
        return False

    @classmethod
    def _format_cmd_multiline(cls, command: str) -> str:
        lines = command.split('\n')
        formatted = []
        indent_level = 0

        for line in lines:
            stripped = line.strip()
            if not stripped:
                formatted.append('')
                continue

            if stripped.startswith(')'):
                indent_level = max(0, indent_level - 1)

            formatted.append(' ' * (indent_level * cls.INDENT_WIDTH) + stripped)

            if stripped.endswith('('):
                indent_level += 1

        return '\n'.join(formatted)

    @classmethod
    def _format_bash_multiline(cls, command: str) -> str:
        lines = cls._split_bash_logical_lines(command)

        if len(lines) <= 1:
            return command

        formatted_lines = []
        indent_level = 0

        indent_increase = {'if', 'then', 'else', 'elif', 'for', 'while', 'until', 'case', 'do', '{'}
        indent_decrease = {'fi', 'done', 'esac', '}', ';;', ';&', ';;&'}
        indent_keep_then_decrease = {'elif', 'else'}

        for line in lines:
            stripped = line.strip()
            if not stripped:
                formatted_lines.append('')
                continue

            first_word = stripped.split()[0] if stripped.split() else ''
            first_word_lower = first_word.lower().rstrip(';')

            if first_word_lower in indent_decrease:
                indent_level = max(0, indent_level - 1)

            formatted_lines.append(' ' * (indent_level * cls.INDENT_WIDTH) + stripped)

            if first_word_lower in indent_increase and first_word_lower != 'then':
                if first_word_lower in ('if', 'elif'):
                    if not stripped.rstrip().endswith('then') and not stripped.rstrip().endswith('then;'):
                        indent_level += 1
                elif first_word_lower == 'else':
                    if stripped.rstrip().endswith('then') or stripped.rstrip().endswith('then;'):
                        indent_level += 1
                else:
                    indent_level += 1

            if stripped.rstrip().endswith('then') or stripped.rstrip().endswith('then;'):
                indent_level += 1

            if first_word_lower in indent_keep_then_decrease:
                pass

        return '\n'.join(formatted_lines)

    @classmethod
    def _format_python_multiline(cls, command: str) -> str:
        lines = command.split('\n')
        formatted_lines = []
        indent_level = 0

        for line in lines:
            stripped = line.rstrip()
            if not stripped:
                formatted_lines.append('')
                continue

            first_word = stripped.split(':')[0].split()[0] if ':' in stripped else stripped.split()[0] if stripped.split() else ''

            if first_word in ('else', 'elif', 'except', 'finally'):
                indent_level = max(0, indent_level - 1)

            formatted_lines.append(' ' * (indent_level * cls.INDENT_WIDTH) + stripped)

            if stripped.rstrip().endswith(':'):
                indent_level += 1

        return '\n'.join(formatted_lines)

    @classmethod
    def _split_bash_logical_lines(cls, command: str) -> List[str]:
        if '\n' in command:
            lines = command.split('\n')
            result = []
            for line in lines:
                sub_lines = cls._split_line_by_semicolons(line)
                result.extend(sub_lines)
            return result

        return cls._split_line_by_semicolons(command)

    @classmethod
    def _split_line_by_semicolons(cls, line: str) -> List[str]:
        result = []
        current = []
        in_single_quote = False
        in_double_quote = False
        escaped = False

        for char in line:
            if escaped:
                current.append(char)
                escaped = False
                continue

            if char == '\\':
                escaped = True
                current.append(char)
                continue

            if char == "'" and not in_double_quote:
                in_single_quote = not in_single_quote
            elif char == '"' and not in_single_quote:
                in_double_quote = not in_double_quote
            elif char == ';' and not in_single_quote and not in_double_quote:
                if current:
                    result.append(''.join(current).strip())
                    current = []
                continue

            current.append(char)

        if current:
            result.append(''.join(current).strip())

        return [r for r in result if r]

    @classmethod
    def is_multiline_command(cls, command: str) -> bool:
        if not command:
            return False

        if '\n' in command:
            return True

        multiline_indicators = ['if ', 'for ', 'while ', 'until ', 'case ', 'then', 'else', 'elif', 'do', 'done', 'fi', 'esac']
        first_line = command.strip()

        for indicator in multiline_indicators:
            if indicator in first_line.lower():
                return True

        if first_line.lower().startswith('if ') or first_line.lower().startswith('for '):
            if '(' in first_line and ')' not in first_line:
                return True

        return False

    @classmethod
    def decode_history_command(cls, command: str) -> str:
        if not command:
            return command

        decoded = command.replace('^J', '\n')
        decoded = decoded.replace('^M', '\r')
        decoded = decoded.replace('^I', '\t')

        return decoded


# ===================== 多行命令状态（增加深度栈） =====================
@dataclass
class MultiLineState:
    type: str = ""
    delimiter: str = ""
    indent_level: int = 0
    lines: List[str] = field(default_factory=list)
    syntax: str = "bash"
    start_line: str = ""
    ast_valid: bool = True
    ast_error: Optional[str] = None
    function_name: Optional[str] = None
    heredoc_syntax_locked: bool = False
    cmd_type: str = ""
    nest_stack: List[str] = field(default_factory=list)
    indent_stack: List[int] = field(default_factory=list)


# ===================== 多行检测器（增强版）=====================
class MultiLineDetector:

    BASH_MULTILINE_PATTERNS = [
        (r'<<\s*[-]?\s*[\'\"]?(\w+)[\'\"]?', 'heredoc', 'bash'),
        (r'\bif\b\s+.*?;\s*then\b', 'if_fi', 'bash'),
        (r'\belif\b\s+.*?;\s*then\b', 'if_fi', 'bash'),
        (r'\belse\b\s*$', 'if_fi', 'bash'),
        (r'\bfor\b\s+.*?;\s*do\b', 'for_do', 'bash'),
        (r'\bwhile\b\s+.*?;\s*do\b', 'while_do', 'bash'),
        (r'\buntil\b\s+.*?;\s*do\b', 'until_do', 'bash'),
        (r'\bcase\b\s+.*?\bin\b', 'case_esac', 'bash'),
        (r'\bfunction\b\s+\w+\s*\{?\s*$', 'function', 'bash'),
        (r'\{\s*$', 'brace_block', 'bash'),
        (r'\(\s*$', 'subshell', 'bash'),
        (r'\\\s*$', 'continuation', 'bash'),
        (r'\|\s*$', 'pipe_continue', 'bash'),
        (r'(&&|\|\|)\s*$', 'logic_continue', 'bash'),
    ]

    PYTHON_MULTILINE_PATTERNS = [
        (r'@\w+(?:\.\w+)*(?:\([^)]*\))?\s*$', 'python_decorator', 'python'),
        (r'\bif\b[^:]*:\s*$', 'python_block', 'python'),
        (r'\belif\b[^:]*:\s*$', 'python_block', 'python'),
        (r'\belse\b\s*:\s*$', 'python_block', 'python'),
        (r'\bfor\b[^:]*:\s*$', 'python_block', 'python'),
        (r'\bwhile\b[^:]*:\s*$', 'python_block', 'python'),
        (r'def\s+\w+\s*\([^)]*\)\s*(?:->\s*[^:]+)?\s*:\s*$', 'python_function', 'python'),
        (r'async\s+def\s+\w+\s*\([^)]*\)\s*(?:->\s*[^:]+)?\s*:\s*$', 'python_function', 'python'),
        (r'class\s+\w+(?:\([^)]*\))?\s*:\s*$', 'python_class', 'python'),
        (r'\bwith\b[^:]*:\s*$', 'python_block', 'python'),
        (r'\btry\b\s*:\s*$', 'python_block', 'python'),
        (r'\bexcept\b[^:]*:\s*$', 'python_block', 'python'),
        (r'\bfinally\b\s*:\s*$', 'python_block', 'python'),
        (r'\bmatch\b[^:]*:\s*$', 'python_block', 'python'),
        (r'\bcase\b[^:]*:\s*$', 'python_block', 'python'),
        (r'"""\s*$', 'triple_quote', 'python'),
        (r"'''\s*$", 'triple_quote', 'python'),
        (r'[\(\[\{]\s*$', 'bracket_open', 'python'),
        (r'\\\s*$', 'continuation', 'python'),
        (r',\s*$', 'comma_continue', 'python'),
    ]

    JS_MULTILINE_PATTERNS = [
        (r'\b(if|else|for|while|switch|function|class|try|catch|finally)\b[^{]*\{?\s*$', 'js_block', 'javascript'),
        (r'\{\s*$', 'js_brace', 'javascript'),
        (r'\([^)]*$', 'paren_open', 'javascript'),
        (r'\\\s*$', 'continuation', 'javascript'),
        (r'=>\s*$', 'arrow_function', 'javascript'),
    ]

    SQL_MULTILINE_PATTERNS = [
        (r'\b(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP)\b.*$', 'sql_statement', 'sql'),
        (r'\([^)]*$', 'paren_open', 'sql'),
    ]

    C_MULTILINE_PATTERNS = [
        (r'\b(if|else|for|while|do|switch)\b[^{]*\{?\s*$', 'c_block', 'c'),
        (r'\{\s*$', 'c_brace', 'c'),
        (r'\\\s*$', 'continuation', 'c'),
    ]

    CMD_MULTILINE_PATTERNS = [
        (r'\bif\b.*\(\s*$', 'cmd_if', 'cmd'),
        (r'\bfor\b\s+.*?\(\s*$', 'cmd_for', 'cmd'),
        (r'\(\s*$', 'cmd_block', 'cmd'),
    ]

    FISH_MULTILINE_PATTERNS = [
        (r'\bif\b\s+.*(?<!; end)$', 'fish_if', 'fish'),
        (r'\bfor\b\s+\w+\s+in\b.*(?<!; end)$', 'fish_for', 'fish'),
        (r'\bwhile\b\s+.*(?<!; end)$', 'fish_while', 'fish'),
        (r'\bfunction\b\s+\w+(?<!; end)$', 'fish_function', 'fish'),
        (r'\bswitch\b\s+.*(?<!; end)$', 'fish_switch', 'fish'),
        (r'\bbegin\b\s*$', 'fish_begin', 'fish'),
        (r'\\\s*$', 'continuation', 'fish'),
        (r'\|\s*$', 'pipe_continue', 'fish'),
        (r'(&&|\|\|)\s*$', 'logic_continue', 'fish'),
    ]

    SYNTAX_SWITCH_MAP = {
        'python': 'python',
        'python3': 'python',
        'py': 'python',
        'bash': 'bash',
        'sh': 'bash',
        'shell': 'bash',
        'c': 'c',
        'cpp': 'cpp',
        'c++': 'cpp',
        'cxx': 'cpp',
        'java': 'java',
        'javascript': 'javascript',
        'js': 'javascript',
        'node': 'javascript',
        'typescript': 'typescript',
        'ts': 'typescript',
        'go': 'go',
        'rust': 'rust',
        'ruby': 'ruby',
        'perl': 'perl',
        'lua': 'lua',
        'sql': 'sql',
        'html': 'html',
        'css': 'css',
        'yaml': 'yaml',
        'yml': 'yaml',
        'json': 'json',
        'markdown': 'markdown',
        'md': 'markdown',
    }

    NEST_KEYWORD_MAP = {
        'if': 'if',
        'for': 'for',
        'while': 'while',
        'until': 'until',
        'case': 'case',
        'function': 'function',
        '{': 'brace',
        '(': 'paren',
        'do': 'do',
        'then': 'then',
    }

    # Python 终止条件更贴近实际缩进块语义
    # - python_block / python_function / python_class：只有当「非空且非缩进行且不以冒号结尾且不以
    #   . 或 , 或 \ 等续行符结尾」时才视为结束块
    # - 顶层装饰器：直到出现非装饰器、非空行
    # - unclosed_quote：仅当累积文本的引号全部闭合时终止（P2 新增）
    TERMINATORS = {
        'heredoc': lambda state, line: line.strip() == state.delimiter,
        'if_fi': lambda state, line: re.match(r'\s*\bfi\b', line),
        'for_do': lambda state, line: re.match(r'\s*\bdone\b', line),
        'while_do': lambda state, line: re.match(r'\s*\bdone\b', line),
        'until_do': lambda state, line: re.match(r'\s*\bdone\b', line),
        'case_esac': lambda state, line: re.match(r'\s*\besac\b', line),
        'function': lambda state, line: re.match(r'\s*\}', line),
        'brace_block': lambda state, line: re.match(r'\s*\}', line),
        'subshell': lambda state, line: re.match(r'\s*\)', line),
        'continuation': lambda state, line: not line.rstrip().endswith('\\'),
        'pipe_continue': lambda state, line: not line.rstrip().endswith('|') and not line.rstrip().endswith('\\'),
        'logic_continue': lambda state, line: not line.rstrip().endswith('&&') and not line.rstrip().endswith('||') and not line.rstrip().endswith('\\'),
        # Python：顶格 + 非空 + 不是冒号续写 + 不是括号/逗号续写 → 结束块
        'python_block': lambda state, line: (
            line.strip() != ''
            and not line.startswith((' ', '\t'))
            and not line.rstrip().endswith((':', '\\', ',', '(', '[', '{'))
        ),
        'python_function': lambda state, line: (
            line.strip() != ''
            and not line.startswith((' ', '\t'))
            and not line.rstrip().endswith((':', '\\', ',', '(', '[', '{'))
        ),
        'python_class': lambda state, line: (
            line.strip() != ''
            and not line.startswith((' ', '\t'))
            and not line.rstrip().endswith((':', '\\', ',', '(', '[', '{'))
        ),
        'python_decorator': lambda state, line: not (line.strip().startswith('@') or line.strip() == ''),
        'triple_quote': lambda state, line: '"""' in line or "'''" in line,
        'bracket_open': lambda state, line: ASTValidator._is_brackets_closed(line, state.lines),
        'comma_continue': lambda state, line: not line.rstrip().endswith(','),
        'js_block': lambda state, line: re.match(r'\s*\}', line),
        'js_brace': lambda state, line: re.match(r'\s*\}', line),
        'paren_open': lambda state, line: ASTValidator._is_parens_closed(line, state.lines),
        'arrow_function': lambda state, line: line.strip() != '' and not line.strip().endswith('=>'),
        'sql_statement': lambda state, line: line.strip().endswith(';'),
        'c_block': lambda state, line: re.match(r'\s*\}', line),
        'c_brace': lambda state, line: re.match(r'\s*\}', line),
        'cmd_if': lambda state, line: MultiLineDetector._is_cmd_parens_closed(state, line),
        'cmd_for': lambda state, line: MultiLineDetector._is_cmd_parens_closed(state, line),
        'cmd_block': lambda state, line: MultiLineDetector._is_cmd_parens_closed(state, line),
        'fish_if': lambda state, line: re.match(r'\s*end\b', line),
        'fish_for': lambda state, line: re.match(r'\s*end\b', line),
        'fish_while': lambda state, line: re.match(r'\s*end\b', line),
        'fish_function': lambda state, line: re.match(r'\s*end\b', line),
        'fish_switch': lambda state, line: re.match(r'\s*end\b', line),
        'fish_begin': lambda state, line: re.match(r'\s*end\b', line),
        # P2 新增：未闭合引号，直到累积文本中引号全部闭合才终止
        'unclosed_quote': lambda state, line: (
            not MultiLineDetector._has_unclosed_quote('\n'.join(state.lines + [line]))
        ),
    }

    @classmethod
    def _has_unclosed_quote(cls, text: str) -> bool:
        """检测文本里是否有未闭合的引号（考虑转义）。

        P2 新增：识别 echo 后跟未闭合双引号、x = 后跟未闭合单引号
        这类「明显没打完」的输入，避免它们被当作完整命令直接交给 shell。

        规则：
        - 反斜杠转义跳过两字符
        - 单引号内遇到双引号不切换（反之亦然）
        - 三引号（三个双引号 / 三个单引号）按奇偶引号计数即可满足判定
        """
        in_single = False
        in_double = False
        i = 0
        n = len(text)
        while i < n:
            c = text[i]
            if c == '\\' and i + 1 < n:
                i += 2
                continue
            if c == "'" and not in_double:
                in_single = not in_single
            elif c == '"' and not in_single:
                in_double = not in_double
            i += 1
        return in_single or in_double

    @classmethod
    def _has_unclosed_paren(cls, text: str) -> bool:
        """检测是否有未闭合的括号（考虑引号与转义）。

        P2 新增：识别 print(1、echo (a 这类「行中括号未闭合」的输入。
        此前 PYTHON_MULTILINE_PATTERNS 里的 bracket_open 只匹配行尾括号，
        行中的 `print(1` 会漏掉，现在靠这个兜底补上。

        - 引号内部括号不计入深度
        - 反斜杠转义跳过两字符
        - 最终深度 > 0 → 有未闭合
        """
        depth = 0
        in_single = False
        in_double = False
        i = 0
        n = len(text)
        while i < n:
            c = text[i]
            if c == '\\' and i + 1 < n:
                i += 2
                continue
            if c == "'" and not in_double:
                in_single = not in_single
            elif c == '"' and not in_single:
                in_double = not in_double
            elif not in_single and not in_double:
                if c in '([{':
                    depth += 1
                elif c in ')]}':
                    depth -= 1
            i += 1
        return depth > 0

    @classmethod
    def _is_cmd_parens_closed(cls, state: MultiLineState, line: str) -> bool:
        all_text = '\n'.join(state.lines + [line]) if state.lines else line
        balance = 0
        for ch in all_text:
            if ch == '(':
                balance += 1
            elif ch == ')':
                balance -= 1
                if balance < 0:
                    return True
        return balance == 0

    @classmethod
    def detect(cls, line: str, expected_syntax: str = "bash") -> Optional[MultiLineState]:
        line = line.rstrip()

        if expected_syntax == "cmd":
            patterns = cls.CMD_MULTILINE_PATTERNS
            for pattern, ml_type, syntax in patterns:
                match = re.search(pattern, line, re.IGNORECASE)
                if match:
                    state = MultiLineState(
                        type=ml_type,
                        syntax=syntax,
                        start_line=line,
                        indent_level=0,
                        cmd_type=ml_type,
                    )
                    state.nest_stack.append(ml_type)
                    return state
            return None

        if expected_syntax == "fish":
            patterns = cls.FISH_MULTILINE_PATTERNS
        elif expected_syntax == "python":
            patterns = cls.PYTHON_MULTILINE_PATTERNS
        elif expected_syntax in ("c", "cpp", "c++"):
            patterns = cls.C_MULTILINE_PATTERNS
        elif expected_syntax in ("javascript", "js", "node"):
            patterns = cls.JS_MULTILINE_PATTERNS
        elif expected_syntax == "sql":
            patterns = cls.SQL_MULTILINE_PATTERNS
        else:
            patterns = cls.BASH_MULTILINE_PATTERNS

        for pattern, ml_type, syntax in patterns:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                state = MultiLineState(
                    type=ml_type,
                    syntax=syntax,
                    start_line=line,
                    indent_level=cls._get_indent_level(line),
                )

                if ml_type == 'heredoc':
                    delim_match = re.search(r'<<\s*[-]?\s*[\'\"]?(\w+)[\'\"]?', line)
                    if delim_match:
                        state.delimiter = delim_match.group(1)

                if ml_type in ('python_function', 'python_class'):
                    name_match = re.search(r'(?:def|class)\s+(\w+)', line)
                    if name_match:
                        state.function_name = name_match.group(1)

                state.nest_stack.append(ml_type)
                return state

        # ── P2 兜底：所有既定模式未命中，但文本明显没打完 ──
        # 1) 未闭合引号 → unclosed_quote
        if cls._has_unclosed_quote(line):
            state = MultiLineState(
                type='unclosed_quote',
                syntax=expected_syntax or 'bash',
                start_line=line,
                indent_level=cls._get_indent_level(line),
            )
            state.nest_stack.append('unclosed_quote')
            return state

        # 2) 未闭合括号（含行中括号）→ 复用 bracket_open
        if cls._has_unclosed_paren(line):
            state = MultiLineState(
                type='bracket_open',
                syntax=expected_syntax or 'bash',
                start_line=line,
                indent_level=cls._get_indent_level(line),
            )
            state.nest_stack.append('bracket_open')
            return state

        return None

    @classmethod
    def _get_indent_level(cls, line: str) -> int:
        stripped = line.lstrip()
        indent_chars = line[:len(line) - len(stripped)]
        if '\t' in indent_chars:
            return len(indent_chars)
        else:
            return len(indent_chars) // 4

    @classmethod
    def update_depth(cls, state: MultiLineState, line: str) -> Optional[MultiLineState]:
        """
        根据当前行更新嵌套深度栈。

        修复：当检测到新嵌套结构时，新 state 会继承旧 state 的外层 nest_stack，
        避免外层结构信息丢失导致终止判断错乱。
        """
        # 先检查是否开启了新的嵌套结构
        new_state = cls.detect(line, state.syntax)
        if new_state and new_state.type != state.type:
            # 修复：把旧栈复制到新 state，再 push 新类型
            new_state.nest_stack = list(state.nest_stack) + [new_state.type]
            # 保持 lines 引用一致（调用方负责 append）
            new_state.lines = state.lines
            return new_state

        # 检查当前行是否闭合了最内层结构
        if not state.nest_stack:
            return None

        current_type = state.nest_stack[-1]
        if current_type in cls.TERMINATORS:
            if cls.TERMINATORS[current_type](state, line):
                state.nest_stack.pop()
                if not state.nest_stack:
                    return "TERMINATED"
                else:
                    prev_type = state.nest_stack[-1]
                    new_state = MultiLineState(
                        type=prev_type,
                        syntax=state.syntax,
                        start_line=state.start_line,
                        lines=state.lines,
                        indent_level=state.indent_level,
                        nest_stack=state.nest_stack,
                    )
                    return new_state
        return None

    @classmethod
    def is_terminated(cls, state: MultiLineState, line: str) -> bool:
        result = cls.update_depth(state, line)
        return result == "TERMINATED"

    @classmethod
    def detect_heredoc_syntax_switch(cls, line: str) -> Optional[str]:
        match = re.match(r'^\s*#\s*(\w+)\s*$', line.strip())
        if not match:
            return None
        keyword = match.group(1).lower()
        return cls.SYNTAX_SWITCH_MAP.get(keyword)


# ===================== 多行补全器（增强版，基于 Pygments token）=====================
class MultiLineCompleter(Completer):

    BASH_KEYWORDS = [
        'if', 'then', 'else', 'elif', 'fi', 'for', 'do', 'done',
        'while', 'until', 'case', 'esac', 'function', 'select',
        'break', 'continue', 'return', 'exit', 'source', 'export',
        'local', 'readonly', 'declare', 'typeset', 'unset',
        'echo', 'printf', 'test', '[', ']', '[[', ']]',
    ]

    PYTHON_KEYWORDS = [
        'False', 'None', 'True', 'and', 'as', 'assert', 'async', 'await',
        'break', 'class', 'continue', 'def', 'del', 'elif', 'else', 'except',
        'finally', 'for', 'from', 'global', 'if', 'import', 'in', 'is', 'lambda',
        'nonlocal', 'not', 'or', 'pass', 'raise', 'return', 'try', 'while', 'with', 'yield',
        'match', 'case',
    ]

    JS_KEYWORDS = [
        'if', 'else', 'for', 'while', 'do', 'switch', 'case', 'break',
        'continue', 'return', 'function', 'class', 'const', 'let', 'var',
        'try', 'catch', 'finally', 'throw', 'new', 'this', 'super',
        'import', 'export', 'default', 'async', 'await', 'true', 'false', 'null',
    ]

    CMD_KEYWORDS = [
        'if', 'else', 'for', 'do', 'in', 'not', 'exist', 'defined',
        'errorlevel', 'goto', 'call', 'exit', 'echo', 'set', 'rem',
        'pause', 'title', 'color', 'prompt', 'path',
    ]

    PYTHON_BUILTINS = [
        'print', 'len', 'range', 'str', 'int', 'float', 'list', 'dict',
        'set', 'tuple', 'enumerate', 'zip', 'map', 'filter', 'sorted',
        'min', 'max', 'sum', 'abs', 'round', 'type', 'isinstance',
        'open', 'help', 'dir', 'vars', 'locals', 'globals',
        'input', 'eval', 'exec', 'repr', 'hash', 'id', 'chr', 'ord',
    ]

    PYTHON_MODULES = [
        'os', 'sys', 're', 'json', 'time', 'datetime', 'math', 'random',
        'subprocess', 'pathlib', 'argparse', 'logging', 'collections',
        'itertools', 'functools', 'typing', 'requests', 'numpy', 'pandas',
        'csv', 'io', 'socket', 'http', 'urllib', 'xml', 'html', 'email',
    ]

    def __init__(self, syntax: str = "bash", virtual_root: str = ""):
        self.syntax = syntax
        self.virtual_root = virtual_root
        self._pygments_lexer = None
        if HAS_PYGMENTS:
            self._pygments_lexer = self._get_lexer_instance(syntax)

    def _get_lexer_instance(self, syntax: str):
        _ensure_pygments_loaded()
        lexer_map = {
            'python': Python3Lexer,
            'bash': BashLexer,
            'c': CLexer,
            'cpp': CppLexer,
            'javascript': JavaScriptLexer,
            'java': JavaLexer,
            'go': GoLexer,
            'rust': RustLexer,
            'sql': SqlLexer,
            'html': HtmlLexer,
            'css': CssLexer,
            'json': JsonLexer,
            'yaml': YamlLexer,
            'markdown': MarkdownLexer,
        }
        lexer_class = lexer_map.get(syntax.lower())
        if lexer_class:
            return lexer_class()
        return None

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor
        word_start = self._get_word_start(text)
        current_word = text[word_start:]
        start_pos = -len(current_word) if current_word else 0

        if self._looks_like_path(current_word):
            yield from self._complete_path(current_word, start_pos)
            return

        token_type = self._get_token_at_cursor(document)

        if self.syntax == "python":
            yield from self._complete_python_by_context(current_word, start_pos, token_type)
        elif self.syntax in ("bash", "sh"):
            yield from self._complete_keyword(current_word, start_pos, self.BASH_KEYWORDS)
        elif self.syntax in ("javascript", "js"):
            yield from self._complete_keyword(current_word, start_pos, self.JS_KEYWORDS)
        elif self.syntax == "cmd":
            yield from self._complete_keyword(current_word, start_pos, self.CMD_KEYWORDS)
        else:
            yield from self._complete_keyword(current_word, start_pos, self._get_keywords())

    def _get_token_at_cursor(self, document: Document) -> Optional[str]:
        if not self._pygments_lexer or not HAS_PYGMENTS:
            return None
        try:
            code = document.text_before_cursor
            tokens = list(self._pygments_lexer.get_tokens(code))
            if tokens:
                last_token = tokens[-1]
                return str(last_token[0])
        except Exception:
            pass
        return None

    def _complete_python_by_context(self, current_word: str, start_pos: int, token_type: Optional[str]):
        if token_type:
            token_str = token_type.lower()
            if 'keyword.namespace' in token_str or 'name.namespace' in token_str:
                for mod in self.PYTHON_MODULES:
                    if mod.startswith(current_word):
                        yield Completion(mod, start_position=start_pos, display_meta="module", style="ansicyan")
                yield from self._complete_keyword(current_word, start_pos, self.PYTHON_KEYWORDS)
                return
            if 'operator' in token_str and '.' in token_str:
                return
            if 'string' in token_str or 'literal' in token_str:
                return

        for kw in self.PYTHON_KEYWORDS:
            if kw.startswith(current_word):
                yield Completion(kw, start_position=start_pos, display_meta="keyword", style="ansiyellow bold")
        for builtin in self.PYTHON_BUILTINS:
            if builtin.startswith(current_word):
                yield Completion(builtin, start_position=start_pos, display_meta="builtin", style="ansicyan")

    def _get_word_start(self, text: str) -> int:
        i = len(text) - 1
        while i >= 0 and text[i] not in ' \t\n\r;|&(){}[]<>':
            i -= 1
        return i + 1

    def _looks_like_path(self, word: str) -> bool:
        return any(c in word for c in '/\\') or word.startswith('~') or word in ('.', '..')

    def _complete_path(self, current_word: str, start_pos: int):
        try:
            from .com import PathCompleterEngine
            engine = PathCompleterEngine(
                show_hidden=True,
                follow_symlinks=True,
                use_cache=True,
                virtual_root=self.virtual_root
            )
            completions = engine.get_completions(current_word, start_pos)
            for comp_text, display_meta, color, rel_start in completions:
                yield Completion(
                    comp_text,
                    start_position=rel_start,
                    display_meta=display_meta,
                    style=color.split()[0] if color else ""
                )
        except ImportError:
            # 修复：原代码缺 yield from，导致异常分支补全完全失效
            yield from self._simple_path_completion(current_word, start_pos)

    def _simple_path_completion(self, current_word: str, start_pos: int):
        try:
            if current_word.startswith('~'):
                dir_path = str(Path.home())
                prefix = current_word[1:] if len(current_word) > 1 else ""
            else:
                if '/' in current_word or '\\' in current_word:
                    dir_path = os.path.dirname(current_word) or '.'
                    prefix = os.path.basename(current_word)
                else:
                    dir_path = '.'
                    prefix = current_word

            if os.path.exists(dir_path) and os.path.isdir(dir_path):
                for item in os.listdir(dir_path):
                    if item.lower().startswith(prefix.lower()):
                        full_path = os.path.join(dir_path, item)
                        is_dir = os.path.isdir(full_path)
                        text = item + (os.sep if is_dir else "")
                        yield Completion(
                            text,
                            start_position=start_pos,
                            display_meta="dir" if is_dir else "file"
                        )
        except Exception:
            pass

    def _complete_keyword(self, current_word: str, start_pos: int, keywords: List[str]):
        for kw in keywords:
            if not current_word or kw.startswith(current_word):
                yield Completion(
                    kw,
                    start_position=start_pos,
                    display_meta="keyword",
                    style="ansiyellow bold"
                )

    def _get_keywords(self) -> List[str]:
        if self.syntax == "python":
            return self.PYTHON_KEYWORDS
        elif self.syntax in ("javascript", "js", "node"):
            return self.JS_KEYWORDS
        elif self.syntax == "cmd":
            return self.CMD_KEYWORDS
        else:
            return self.BASH_KEYWORDS


# ===================== 多行输入会话（增强智能缩进）=====================
class MultiLineInput:

    MULTILINE_STYLE = Style.from_dict({
        'multiline-prompt': '#00ff00 bold',
        'multiline-text': '#cccccc',
        'error': '#ff0000 bold',
        'warning': '#ffff00',
        'info': '#00aaff',
        'keyword': '#ffff00 bold',
        'string': '#00ff00',
        'comment': '#888888',
        'function': '#00aaff bold',
        'class': '#ff00ff bold',
        'decorator': '#ff8800 bold',
        'number': '#ff8800',
        'operator': '#ff00ff',
        'builtin': '#00ffaa',
    })

    CONTINUATION_PROMPTS = {
        'heredoc': 'heredoc-txt> ',
        'heredoc-python': 'heredoc-python> ',
        'heredoc-bash': 'heredoc-bash> ',
        'heredoc-c': 'heredoc-c> ',
        'heredoc-cpp': 'heredoc-cpp> ',
        'heredoc-java': 'heredoc-java> ',
        'heredoc-javascript': 'heredoc-js> ',
        'heredoc-typescript': 'heredoc-ts> ',
        'heredoc-go': 'heredoc-go> ',
        'heredoc-rust': 'heredoc-rust> ',
        'heredoc-ruby': 'heredoc-ruby> ',
        'heredoc-perl': 'heredoc-perl> ',
        'heredoc-lua': 'heredoc-lua> ',
        'heredoc-sql': 'heredoc-sql> ',
        'heredoc-html': 'heredoc-html> ',
        'heredoc-css': 'heredoc-css> ',
        'heredoc-yaml': 'heredoc-yaml> ',
        'heredoc-json': 'heredoc-json> ',
        'heredoc-markdown': 'heredoc-md> ',
        'if_fi': 'if> ',
        'for_do': 'for> ',
        'while_do': 'while> ',
        'until_do': 'until> ',
        'case_esac': 'case> ',
        'function': 'func> ',
        'brace_block': '{}> ',
        'subshell': '()> ',
        'continuation': '> ',
        'pipe_continue': 'pipe> ',
        'logic_continue': '&&> ',
        'python_block': '... ',
        'python_function': 'def> ',
        'python_class': 'class> ',
        'python_decorator': '@> ',
        'triple_quote': '""" ',
        'bracket_open': '(> ',
        'comma_continue': ',> ',
        'js_block': '{> ',
        'js_brace': '{> ',
        'paren_open': '(> ',
        'arrow_function': '=> ',
        'sql_statement': 'SQL> ',
        'c_block': '{> ',
        'c_brace': '{> ',
        'cmd_if': 'if> ',
        'cmd_for': 'for> ',
        'cmd_block': '()> ',
        # P2 新增：未闭合引号时的续行提示符
        'unclosed_quote': 'quote> ',
    }

    def __init__(self, syntax: str = "auto", virtual_root: str = ""):
        self.default_syntax = syntax
        self.current_syntax = syntax if syntax != "auto" else "bash"
        self.virtual_root = virtual_root
        self._current_state: Optional[MultiLineState] = None

        self.kb = self._create_key_bindings()
        self.lexer = self._get_pygments_lexer(self.current_syntax)
        self.completer = MultiLineCompleter(self.current_syntax, virtual_root)
        self.indent_width = 4

    def _detect_syntax_from_content(self, content: str) -> str:
        if self.default_syntax == "auto":
            detected = SmartSyntaxDetector.detect(content)
            return detected.value
        return self.default_syntax

    def _get_pygments_lexer(self, syntax: str):
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

    def _create_key_bindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add('c-d')
        def _(event):
            buffer = event.app.current_buffer
            if buffer.text.strip() == '':
                buffer.text = ''
                buffer.validate_and_handle()
                event.app.exit(result=None)

        @kb.add('c-c')
        def _(event):
            event.app.exit(result='__CANCEL__')

        @kb.add('tab')
        def _(event):
            buffer = event.app.current_buffer
            buffer.start_completion(select_first=False)

        @kb.add('s-tab')
        def _(event):
            buffer = event.app.current_buffer
            buffer.complete_previous()

        @kb.add('c-l')
        def _(event):
            buffer = event.app.current_buffer
            if self._current_state:
                self._validate_syntax(buffer.text, self._current_state)

        @kb.add('enter')
        def _(event):
            buffer = event.app.current_buffer
            current_line = buffer.document.current_line
            indent = self._compute_smart_indent(current_line, buffer.document)
            buffer.insert_text('\n' + ' ' * indent)

        return kb

    def _compute_smart_indent(self, current_line: str, document: Document) -> int:
        base_indent = len(current_line) - len(current_line.lstrip())
        stripped = current_line.lstrip()

        if not HAS_PYGMENTS or not self.lexer:
            return self._simple_indent_rule(current_line)

        try:
            if self.current_syntax in ('python', 'python3'):
                if stripped.endswith(':'):
                    first_word = stripped.split(':')[0].split()[0] if ':' in stripped else ''
                    if first_word in ('else', 'elif', 'except', 'finally'):
                        return max(0, base_indent - self.indent_width)
                    return base_indent + self.indent_width
                open_count = stripped.count('(') + stripped.count('[') + stripped.count('{')
                close_count = stripped.count(')') + stripped.count(']') + stripped.count('}')
                if open_count > close_count:
                    return base_indent + self.indent_width
                if stripped.endswith('\\'):
                    return base_indent + self.indent_width
                return base_indent
            elif self.current_syntax in ('bash', 'sh'):
                if re.search(r'\b(do|then)\b\s*$', stripped):
                    return base_indent + self.indent_width
                elif re.search(r'\b(else|elif)\b\s*$', stripped):
                    return max(0, base_indent - self.indent_width)
                return base_indent
            else:
                if stripped.endswith('{'):
                    return base_indent + self.indent_width
                elif stripped.startswith('}'):
                    return max(0, base_indent - self.indent_width)
                return base_indent
        except Exception:
            pass

        return self._simple_indent_rule(current_line)

    def _simple_indent_rule(self, current_line: str) -> int:
        stripped = current_line.lstrip()
        base_indent = len(current_line) - len(stripped)

        if stripped.endswith(':'):
            first_word = stripped.split(':')[0].split()[0] if ':' in stripped else ''
            if first_word in ('else', 'elif', 'except', 'finally', 'case'):
                return max(0, base_indent - self.indent_width)
            return base_indent + self.indent_width
        if stripped.endswith('{'):
            return base_indent + self.indent_width
        if stripped.endswith('('):
            return base_indent + self.indent_width
        if stripped.startswith('}'):
            return max(0, base_indent - self.indent_width)
        if stripped.startswith(')'):
            return max(0, base_indent - self.indent_width)
        return base_indent

    def _validate_syntax(self, code: str, state: MultiLineState):
        full_code = '\n'.join(state.lines + [code]) if state.lines else code
        is_valid, error_msg, line_num = ASTValidator.validate(full_code, state.syntax)

        state.ast_valid = is_valid
        state.ast_error = error_msg if not is_valid else None

        if not is_valid and error_msg:
            print(f"\r\033[K\033[31m语法错误 (行 {line_num}): {error_msg}\033[0m")

    def _get_heredoc_prompt(self, state: MultiLineState) -> str:
        if state.type == 'heredoc':
            key = f'heredoc-{state.syntax}'
            return self.CONTINUATION_PROMPTS.get(key, 'heredoc> ')
        return self.CONTINUATION_PROMPTS.get(state.type, '> ')

    def _get_prompt_text(self, state: MultiLineState):
        """构造续行提示符。
    
        修复（ANSI 泄漏专项 · P3）：
        - 不再手写 \\033[..m ANSI 转义序列。prompt_toolkit 的 prompt()
          不会把 ANSI 当控制码解析，只会原样输出 → 用户看到
          ^[[32m✓ heredoc-bash> ^[[0m 这样的乱码。
        - 改用纯字符串 + 状态标记字符（✓ / !），ptk 按普通文本渲染，
          干净、跨终端一致。想要颜色可以改成 FormattedText 并配套
          一份样式表，但没必要为了两个标记上样式。
        """
        prompt = self._get_heredoc_prompt(state)
    
        # 状态标记：✓ 表示语法已完整，! 表示有语法错误，空表示普通续行
        if not state.ast_valid and state.ast_error:
            marker = "! "
        elif state.ast_valid and len(state.lines) > 2:
            marker = "✓ "
        else:
            marker = ""
    
        if state.function_name:
            return f"{state.function_name} ▶ {marker}{prompt}"
        return f"{marker}{prompt}"

    def process_line(self, line: str, state: MultiLineState) -> Tuple[bool, Optional[MultiLineState]]:
        if state.nest_stack:
            current_type = state.nest_stack[-1]
            if current_type in MultiLineDetector.TERMINATORS:
                if MultiLineDetector.TERMINATORS[current_type](state, line):
                    state.nest_stack.pop()
                    if not state.nest_stack:
                        return True, None
                    else:
                        prev_type = state.nest_stack[-1]
                        new_state = MultiLineState(
                            type=prev_type,
                            syntax=state.syntax,
                            start_line=state.start_line,
                            lines=state.lines,
                            indent_level=state.indent_level,
                            nest_stack=state.nest_stack,
                        )
                        return False, new_state

        new_state = MultiLineDetector.detect(line, state.syntax)
        if new_state and new_state.type != state.type:
            new_state.lines = state.lines.copy()
            new_state.nest_stack = state.nest_stack.copy()
            new_state.nest_stack.append(new_state.type)
            return False, new_state

        return False, None

    def read_multiline(self, initial_line: str, state: MultiLineState) -> Optional[str]:
        self._current_state = state

        if self.default_syntax == "auto" and state.syntax == "bash":
            detected = SmartSyntaxDetector.detect(initial_line)
            if detected != SyntaxType.UNKNOWN and detected.value != state.syntax:
                state.syntax = detected.value

        lines = [initial_line]
        state.lines = lines

        if state.syntax and state.syntax != self.current_syntax:
            self.current_syntax = state.syntax
            self.lexer = self._get_pygments_lexer(state.syntax)
            self.completer.syntax = state.syntax

        self._validate_syntax(initial_line, state)

        history = InMemoryHistory()

        session = PromptSession(
            history=history,
            key_bindings=self.kb,
            style=self.MULTILINE_STYLE,
            lexer=self.lexer,
            completer=self.completer,
            complete_while_typing=True,
            multiline=False,
            reserve_space_for_menu=4,
        )

        while True:
            try:
                prompt_text = self._get_prompt_text(state)
                line = session.prompt(
                    prompt_text,
                    auto_suggest=AutoSuggestFromHistory(),
                )

                if line is None:
                    return '\n'.join(lines) if lines else None

                if line.strip() == '__CANCEL__':
                    return None

                terminated, new_state = self.process_line(line, state)
                lines.append(line)
                state.lines = lines

                if terminated:
                    return '\n'.join(lines)
                elif new_state is not None:
                    state = new_state
                    self.current_syntax = state.syntax
                    self.lexer = self._get_pygments_lexer(state.syntax)
                    self.completer.syntax = state.syntax
                else:
                    if state.type == 'heredoc' and not state.heredoc_syntax_locked:
                        if MultiLineDetector.is_terminated(state, line):
                            return '\n'.join(lines)

                        new_syntax = MultiLineDetector.detect_heredoc_syntax_switch(line)
                        if new_syntax and new_syntax != state.syntax:
                            state.syntax = new_syntax
                            state.heredoc_syntax_locked = True
                            self.current_syntax = new_syntax
                            self.lexer = self._get_pygments_lexer(new_syntax)
                            self.completer.syntax = new_syntax
                            continue

                self._validate_syntax(line, state)

            except KeyboardInterrupt:
                return None
            except EOFError:
                return '\n'.join(lines) if lines else None

    def format_with_syntax(self, code: str) -> str:
        if not HAS_PYGMENTS or not self.lexer:
            return code

        try:
            lexer = self.lexer.lexer
            return highlight(code, lexer, TerminalFormatter())
        except Exception:
            return code


# ===================== 多行命令处理函数（简化） =====================
def handle_multiline_input(
    user_input: str,
    syntax: str = "auto",
    virtual_root: str = "",
    prompt_func: Optional[Callable] = None,
    input_func: Optional[Callable] = None,
) -> Tuple[str, bool]:
    if not user_input:
        return user_input, False

    if user_input.rstrip().endswith('\\'):
        return user_input.rstrip()[:-1], False

    detected_syntax = syntax
    if syntax == "auto":
        if _get_terminal_type() == 'cmd':
            detected_syntax = "cmd"
        else:
            detected = SmartSyntaxDetector.detect_from_command(user_input)
            if detected == SyntaxType.UNKNOWN:
                detected = SmartSyntaxDetector.detect(user_input)
            detected_syntax = detected.value if detected != SyntaxType.UNKNOWN else "bash"

    state = MultiLineDetector.detect(user_input, detected_syntax)

    if state is None:
        return user_input, True

    if input_func is not None:
        return _simple_multiline_input(user_input, state, input_func)
    else:
        ml_input = MultiLineInput(syntax=syntax, virtual_root=virtual_root)
        result = ml_input.read_multiline(user_input, state)

        if result is None or result == '__CANCEL__':
            return '', True
        return result, True


def _simple_multiline_input(
    initial_line: str,
    state: MultiLineState,
    input_func: Callable = input,
) -> Tuple[str, bool]:
    lines = [initial_line]
    # P2 修复：让 state.lines 从首行起累积，否则基于 state.lines 的终止条件
    # （如 unclosed_quote / bracket_open）永远只看到当前行，无法正确判断闭合
    state.lines = lines

    prompts = {
        'heredoc': 'heredoc> ',
        'if_fi': 'if> ',
        'for_do': 'for> ',
        'while_do': 'while> ',
        'until_do': 'until> ',
        'case_esac': 'case> ',
        'function': 'func> ',
        'brace_block': '{}> ',
        'subshell': '()> ',
        'continuation': '> ',
        'pipe_continue': 'pipe> ',
        'logic_continue': '> ',
        'python_block': '... ',
        'python_function': 'def> ',
        'python_class': 'class> ',
        'python_decorator': '@> ',
        'triple_quote': '""" ',
        'bracket_open': ')> ',
        'c_block': '{> ',
        'cmd_if': 'if> ',
        'cmd_for': 'for> ',
        'cmd_block': '()> ',
        # P2 新增
        'unclosed_quote': 'quote> ',
    }

    prompt = prompts.get(state.type, '> ')

    while True:
        try:
            line = input_func(prompt)

            if line.strip() == '':
                if state.type in ('continuation', 'pipe_continue', 'logic_continue'):
                    continue
                return '\n'.join(lines), True

            lines.append(line)

            if MultiLineDetector.is_terminated(state, line):
                return '\n'.join(lines), True

        except KeyboardInterrupt:
            return '', True
        except EOFError:
            return '\n'.join(lines), True


# ===================== 语法检测函数 =====================
def detect_syntax_from_command(command: str) -> str:
    term_type = _get_terminal_type()
    if term_type == 'cmd':
        return 'cmd'
    if term_type == 'fish':
        return 'fish'
    detected = SmartSyntaxDetector.detect_from_command(command)
    return detected.value if detected != SyntaxType.UNKNOWN else 'bash'


def detect_syntax_from_content(content: str) -> str:
    detected = SmartSyntaxDetector.detect(content)
    return detected.value if detected != SyntaxType.UNKNOWN else 'bash'


def detect_syntax_smart(content: str, context: Optional[str] = None) -> Tuple[str, float]:
    detected, confidence = SmartSyntaxDetector.detect_with_confidence(content)
    return detected.value, confidence


def get_continuation_lexer(state: MultiLineState):
    _ensure_pygments_loaded()
    if not HAS_PYGMENTS:
        return None

    lexer_map = {
        'bash': BashLexer,
        'sh': BashLexer,
        'shell': BashLexer,
        'python': Python3Lexer,
        'python3': Python3Lexer,
        'c': CLexer,
        'cpp': CppLexer,
        'c++': CppLexer,
        'java': JavaLexer,
        'javascript': JavaScriptLexer,
        'go': GoLexer,
        'rust': RustLexer,
        'sql': SqlLexer,
    }

    lexer_class = lexer_map.get(state.syntax.lower())
    if lexer_class:
        return PygmentsLexer(lexer_class)

    return None


def validate_code_syntax(code: str, syntax: str) -> Tuple[bool, Optional[str], Optional[int]]:
    return ASTValidator.validate(code, syntax)


# ===================== 辅助函数 =====================
def is_python_code_complete(code: str) -> bool:
    """判断 Python 代码是否是完整可提交的（严格模式，不再把不完整误判为完整）"""
    return ASTValidator.is_complete_python(code)


def auto_detect_and_validate(code: str) -> Tuple[bool, str, Optional[str], Optional[int]]:
    syntax = detect_syntax_from_content(code)
    is_valid, error_msg, line_num = ASTValidator.validate(code, syntax)
    return is_valid, syntax, error_msg, line_num


# ===================== 导出 =====================
__all__ = [
    'MultiLineDetector',
    'MultiLineState',
    'MultiLineInput',
    'MultiLineCompleter',
    'MultiLineFormatter',
    'handle_multiline_input',
    'detect_syntax_from_command',
    'detect_syntax_from_content',
    'detect_syntax_smart',
    'auto_detect_and_validate',
    'get_continuation_lexer',
    'validate_code_syntax',
    'SmartSyntaxDetector',
    'SyntaxType',
    'ASTValidator',
    'HAS_PYGMENTS',
    'is_python_code_complete',
]

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
            return initial_text

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