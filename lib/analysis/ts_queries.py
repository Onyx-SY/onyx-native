"""
ts_queries.py — AST 符号查询引擎

两层架构：
  1. tree-sitter 模式（若语言 grammar 可用）→ 精确 AST 查询
  2. 正则回退模式（零依赖）→ 基于启发式的符号提取

对外接口：
  - get_symbols(code, language) → list of {name, kind, line, col}
  - find_definition(code, name, language) → location or None
  - find_references(code, name, language) → list of locations
  - outline(code, language) → text outline
"""

import re
from typing import List, Optional, Dict, Any

# ──────────────────────────── 正则回退 ────────────────────────────

_PATTERNS = {
    "python": {
        "function": re.compile(r'^(\s*)def\s+(\w+)\s*\(', re.MULTILINE),
        "class":    re.compile(r'^(\s*)class\s+(\w+)\s*[:(]', re.MULTILINE),
        "async_fn": re.compile(r'^(\s*)async\s+def\s+(\w+)\s*\(', re.MULTILINE),
        "decorator": re.compile(r'^(\s*)@(\w+)', re.MULTILINE),
    },
    "javascript": {
        "function": re.compile(r'(?:function\s+(\w+)|(\w+)\s*[:=]\s*(?:async\s*)?function|const\s+(\w+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>)', re.MULTILINE),
        "class":    re.compile(r'class\s+(\w+)', re.MULTILINE),
        "arrow_fn": re.compile(r'const\s+(\w+)\s*=\s*(?:async\s*)?\(', re.MULTILINE),
    },
    "typescript": {
        "function": re.compile(r'(?:function\s+(\w+)|(\w+)\s*[:=]\s*(?:async\s*)?function|const\s+(\w+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>)', re.MULTILINE),
        "class":    re.compile(r'class\s+(\w+)', re.MULTILINE),
        "interface": re.compile(r'interface\s+(\w+)', re.MULTILINE),
        "type":     re.compile(r'type\s+(\w+)\s*=', re.MULTILINE),
        "arrow_fn": re.compile(r'const\s+(\w+)\s*=\s*(?:async\s*)?\(', re.MULTILINE),
    },
    "go": {
        "function": re.compile(r'^func\s+(?:\([^)]*\)\s+)?(\w+)\s*\(', re.MULTILINE),
        "struct":   re.compile(r'^type\s+(\w+)\s+struct', re.MULTILINE),
        "interface": re.compile(r'^type\s+(\w+)\s+interface', re.MULTILINE),
    },
    "rust": {
        "function": re.compile(r'^(\s*)fn\s+(\w+)\s*[<(]', re.MULTILINE),
        "struct":   re.compile(r'^(\s*)struct\s+(\w+)', re.MULTILINE),
        "trait":    re.compile(r'^(\s*)trait\s+(\w+)', re.MULTILINE),
        "impl":     re.compile(r'^(\s*)impl\s+(\w+)', re.MULTILINE),
        "enum":     re.compile(r'^(\s*)enum\s+(\w+)', re.MULTILINE),
    },
    "c": {
        "function": re.compile(r'^\w+(?:\s*\*)?\s+(\w+)\s*\([^)]*\)\s*\{', re.MULTILINE),
        "struct":   re.compile(r'^\s*(?:typedef\s+)?struct\s+(\w+)', re.MULTILINE),
    },
    "cpp": {
        "function": re.compile(r'^\w+(?:\s*\*)?\s+(?:\w+::)?(\w+)\s*\([^)]*\)\s*(?:const\s*)?\{', re.MULTILINE),
        "class":    re.compile(r'^\s*class\s+(\w+)', re.MULTILINE),
        "struct":   re.compile(r'^\s*struct\s+(\w+)', re.MULTILINE),
    },
}

_REF_PATTERNS = {
    "python": re.compile(r'\b(\w+)\s*\('),
    "javascript": re.compile(r'\b(\w+)\s*\('),
    "typescript": re.compile(r'\b(\w+)\s*\('),
    "go":       re.compile(r'\b(\w+)\s*\('),
    "rust":     re.compile(r'\b(\w+)\s*(?:\(|::)'),
    "c":        re.compile(r'\b(\w+)\s*\('),
    "cpp":      re.compile(r'\b(\w+)\s*(?:\(|::)'),
}

_EXTENSION_MAP = {
    ".py": "python", ".pyw": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp",
}

_COMMENT_PATTERNS = {
    "python": re.compile(r'#.*$|"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'', re.MULTILINE),
    "javascript": re.compile(r'//.*$|/\*[\s\S]*?\*/', re.MULTILINE),
    "typescript": re.compile(r'//.*$|/\*[\s\S]*?\*/', re.MULTILINE),
    "go":       re.compile(r'//.*$|/\*[\s\S]*?\*/', re.MULTILINE),
    "rust":     re.compile(r'//.*$|/\*[\s\S]*?\*/', re.MULTILINE),
    "c":        re.compile(r'//.*$|/\*[\s\S]*?\*/', re.MULTILINE),
    "cpp":      re.compile(r'//.*$|/\*[\s\S]*?\*/', re.MULTILINE),
}


# ──────────────────────────── 公共 API ────────────────────────────

def language_for_path(path: str) -> Optional[str]:
    """根据文件扩展名推断语言。"""
    _, ext = os.path.splitext(path)
    return _EXTENSION_MAP.get(ext.lower())

def strip_comments(code: str, language: str) -> str:
    """移除注释，返回干净的代码文本。"""
    pat = _COMMENT_PATTERNS.get(language)
    if pat:
        return pat.sub("", code)
    return code

def get_symbols(code: str, language: str) -> List[Dict[str, Any]]:
    """
    提取代码中的所有符号定义。

    返回: [{name, kind, line, col}, ...]
    """
    symbols = []
    patterns = _PATTERNS.get(language, {})
    for kind, pat in patterns.items():
        for m in pat.finditer(code):
            # 取最后一个捕获组（所有模式都把名字放最后）
            name = m.group(m.lastindex) if m.lastindex else None
            if name:
                line = code[:m.start()].count("\n") + 1
                col = m.start() - code[:m.start()].rfind("\n") - 1
                symbols.append({"name": name, "kind": kind, "line": line, "col": max(col, 0)})
    # 去重（同名同行的合并）
    seen = set()
    unique = []
    for s in symbols:
        key = (s["name"], s["line"])
        if key not in seen:
            seen.add(key)
            unique.append(s)
    return unique

def find_definition(code: str, name: str, language: str) -> Optional[Dict[str, Any]]:
    """查找符号定义位置。"""
    for s in get_symbols(code, language):
        if s["name"] == name:
            return s
    return None

def find_references(code: str, name: str, language: str) -> List[Dict[str, int]]:
    """查找符号引用位置（函数调用、变量使用等）。"""
    refs = []
    pat = _REF_PATTERNS.get(language)
    if not pat:
        return refs
    seen_defs = {s["name"] for s in get_symbols(code, language)}
    # 只搜索已定义的符号的引用
    if name not in seen_defs:
        return refs
    for m in pat.finditer(code):
        if m.group(1) == name:
            line = code[:m.start()].count("\n") + 1
            col = m.start() - code[:m.start()].rfind("\n") - 1
            refs.append({"line": line, "col": max(col, 0)})
    return refs

def outline(code: str, language: str) -> str:
    """生成代码大纲文本。"""
    symbols = get_symbols(code, language)
    if not symbols:
        return "(no symbols found)"
    lines = []
    for s in symbols:
        lines.append(f"  {s['kind']:12s} {s['name']:30s} L{s['line']}")
    return "\n".join(lines)

# 延迟导入 os（避免循环）
import os

# ──────────────────────────── tree-sitter 检测 ───────────────────

_TS_AVAILABLE = False
_TS_PARSER = None

def _init_ts():
    """尝试初始化 tree-sitter（按需检测）。"""
    global _TS_AVAILABLE, _TS_PARSER
    if _TS_PARSER is not None:
        return
    try:
        import tree_sitter as ts
        _TS_PARSER = ts.Parser()
        _TS_AVAILABLE = True
    except Exception:
        _TS_AVAILABLE = False
        _TS_PARSER = False  # 标记已尝试过

def _ts_language(lang: str):
    """按语言名加载 tree-sitter grammar。"""
    try:
        if lang == "python":
            import tree_sitter_python as mod
            return mod.language()
        elif lang == "javascript":
            import tree_sitter_javascript as mod
            return mod.language()
        elif lang == "typescript":
            import tree_sitter_typescript as mod
            return mod.language_typescript()
        elif lang == "go":
            import tree_sitter_go as mod
            return mod.language()
        elif lang == "rust":
            try:
                import tree_sitter_rust as mod
                return mod.language()
            except Exception:
                return None
        elif lang == "c":
            import tree_sitter_c as mod
            return mod.language()
        elif lang == "cpp":
            try:
                import tree_sitter_cpp as mod
                return mod.language()
            except Exception:
                return None
    except Exception:
        return None

def get_symbols_ts(code: str, language: str) -> Optional[List[Dict]]:
    """tree-sitter 模式（若可用且 grammar 已装）。"""
    _init_ts()
    if not _TS_AVAILABLE or _TS_PARSER is False:
        return None
    lang_obj = _ts_language(language)
    if lang_obj is None:
        return None
    try:
        _TS_PARSER.set_language(lang_obj)
        tree = _TS_PARSER.parse(code.encode("utf-8"))
        # 从 AST 提取命名定义
        symbols = []
        cursor = tree.walk()
        _walk_ts(cursor, symbols)
        return symbols
    except Exception:
        return None

def _walk_ts(cursor, symbols: list, depth=0):
    """遍历 tree-sitter AST 收集命名节点。"""
    if depth > 50:
        return
    node = cursor.node
    # 函数/类定义节点
    kind = node.type
    name_node = None
    is_def = False
    if kind in ("function_definition", "method_definition"):
        is_def = True
        for child in node.children:
            if child.type == "identifier":
                name_node = child
                break
            elif child.type == "name":
                name_node = child
                break
    elif kind in ("class_definition", "class_declaration"):
        is_def = True
        for child in node.children:
            if child.type == "name":
                name_node = child
                break
    elif kind == "decorated_definition":
        # 装饰器定义，进子节点
        pass
    if name_node and is_def:
        name = code_slice(node, name_node)
        symbols.append({
            "name": name,
            "kind": kind,
            "line": node.start_point[0] + 1,
            "col": node.start_point[1],
        })
    # 递归遍历
    if cursor.goto_first_child():
        _walk_ts(cursor, symbols, depth + 1)
        while cursor.goto_next_sibling():
            _walk_ts(cursor, symbols, depth + 1)
        cursor.goto_parent()

def code_slice(root_node, node) -> str:
    """从源码中提取节点文本。"""
    try:
        return root_node.text[node.start_byte:node.end_byte].decode("utf-8")
    except Exception:
        return "?"
