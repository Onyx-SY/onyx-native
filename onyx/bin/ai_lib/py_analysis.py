"""
Python 内置代码分析 — 零依赖，纯 ast 实现。

定位：纯辅助工具，零主观判断、零决策。
  1. exec_py_diagnostics — 纯粹的编译尝试：仅在 compile() 失败（真实语法/编译错误）时报告错误。
     不输出任何启发式提示（未使用导入 / 裸 except / 不可达代码 / 死代码等），因此不存在误报。
  2. exec_py_symbols     — 唯一用途：归拢文件中所有定义的函数/类及其精确位置（path:line）。

所有输出为中英双语（通过 i18n 模块）。
"""

import ast
import os

from .i18n import _ as _t


_MAX_PY_SIZE = 1024 * 1024 * 2  # 2MB


# ── 共享辅助函数 ──

def read_py_source(path: str) -> tuple:
    """读取 .py 文件内容，返回 (source, None) 或 (None, 错误信息)。"""
    if not os.path.isfile(path):
        return None, _t("py_file_not_found", "bilingual", path=path)
    if not path.endswith(".py"):
        return None, _t("py_only_supported", "bilingual")
    size = os.path.getsize(path)
    if size > _MAX_PY_SIZE:
        return None, _t("py_file_too_large", "bilingual", size=size / 1024 / 1024)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(), None
    except Exception as e:
        return None, _t("py_read_failed", "bilingual", err=e)


def parse_py_source(source: str, path: str = "") -> tuple:
    """解析 Python 源码为 AST，返回 (tree, None) 或 (None, 错误信息)。"""
    try:
        return ast.parse(source, filename=path), None
    except SyntaxError as e:
        return None, _format_syntax_error(e)


def compile_py_source(source: str, path: str = "") -> tuple:
    """真正的编译尝试：compile() 成功返回 (True, None)，失败返回 (False, 错误信息)。"""
    try:
        compile(source, path, "exec")
        return True, None
    except SyntaxError as e:
        return False, _format_syntax_error(e)


def _format_syntax_error(e: SyntaxError) -> str:
    line = getattr(e, "lineno", None)
    text = (getattr(e, "text", "") or "").strip()
    return _t(
        "py_syntax_error", "bilingual",
        line=line if line is not None else "?",
        msg=getattr(e, "msg", "?"),
        text=text,
    )


def _node_end(node) -> int:
    return getattr(node, 'end_lineno', node.lineno)


# ── py_diagnostics — 纯编译尝试 ──

def exec_py_diagnostics(path: str) -> str:
    """检查 Python 文件：仅当真实编译失败（语法错误）时报告错误。

    这是纯粹的编译尝试 —— 不做任何启发式判断（未使用导入、裸 except、
    不可达代码等一律不报告），因此不存在误报。
    """
    source, err = read_py_source(path)
    if err:
        return err
    if not source.strip():
        return _t("py_diag_empty", "bilingual", path=path)

    ok, err = compile_py_source(source, path)
    if not ok:
        return err
    return _t("py_diag_ok", "bilingual", path=path)


# ── py_symbols — 归拢所有函数/类定义及其位置（唯一用途）──

def exec_py_symbols(path: str) -> str:
    """提取文件中所有定义的函数/类及其精确位置。"""
    source, err = read_py_source(path)
    if err:
        return err
    if not source.strip():
        return _t("py_sym_empty", "bilingual", path=path)

    tree, err = parse_py_source(source, path)
    if err:
        return err

    def _decorators(node) -> str:
        parts = []
        for dec in getattr(node, 'decorator_list', []):
            d = dec.func if isinstance(dec, ast.Call) else dec
            if isinstance(d, ast.Attribute):
                parts.append(f"@{d.attr}")
            elif isinstance(d, ast.Name):
                parts.append(f"@{d.id}")
            if len(parts) >= 2:
                break
        return " ".join(parts) + " " if parts else ""

    def _return_type(node) -> str:
        ann = getattr(node, 'returns', None)
        if ann is None:
            return ""
        if isinstance(ann, ast.Name):
            return f" -> {ann.id}"
        if isinstance(ann, ast.Subscript):
            return " -> […]"
        if isinstance(ann, ast.Constant):
            return f" -> {ann.value}"
        return " -> ?"

    def _params(node) -> str:
        parts = []
        for arg in node.args.args:
            s = arg.arg
            if arg.annotation:
                if isinstance(arg.annotation, ast.Name):
                    s += f": {arg.annotation.id}"
                elif isinstance(arg.annotation, ast.Subscript):
                    s += ": […]"
                else:
                    s += ": ?"
            parts.append(s)
            if len(parts) >= 6:
                parts.append("…")
                break
        if node.args.vararg:
            parts.append(f"*{node.args.vararg.arg}")
        if node.args.kwonlyargs:
            for a in node.args.kwonlyargs:
                parts.append(f"{a.arg}=…")
                if len(parts) >= 8:
                    break
        if node.args.kwarg:
            parts.append(f"**{node.args.kwarg.arg}")
        return ", ".join(parts)

    symbols = []

    def _visit(node, depth=0):
        ind = "  " * depth
        loc = f"@ {path}:{node.lineno}-{_node_end(node)}"
        if isinstance(node, ast.ClassDef):
            symbols.append(f"{ind}◈ {_decorators(node)}class `{node.name}` {loc}")
            _walk_stmts(node.body, depth + 1)
        elif isinstance(node, ast.FunctionDef):
            symbols.append(
                f"{ind}{_decorators(node)}ƒ def `{node.name}({_params(node)})"
                f"{_return_type(node)}` {loc}"
            )
            _walk_stmts(node.body, depth + 1)
        elif isinstance(node, ast.AsyncFunctionDef):
            symbols.append(
                f"{ind}{_decorators(node)}ƒ async `{node.name}({_params(node)})"
                f"{_return_type(node)}` {loc}"
            )
            _walk_stmts(node.body, depth + 1)

    def _walk_stmts(stmts, depth):
        """递归遍历语句块，捕捉 if/for/try/match 等分支内嵌套的定义。"""
        for stmt in stmts:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                _visit(stmt, depth)
                continue
            for attr in ("body", "orelse", "finalbody"):
                sub = getattr(stmt, attr, None)
                if isinstance(sub, list):
                    _walk_stmts(sub, depth)
            handlers = getattr(stmt, "handlers", None)
            if isinstance(handlers, list):
                for h in handlers:
                    _walk_stmts(h.body, depth)
            # Python 3.10+ match-case 分支
            cases = getattr(stmt, "cases", None)
            if isinstance(cases, list):
                for c in cases:
                    _walk_stmts(c.body, depth)

    _walk_stmts(tree.body, 0)

    if not symbols:
        return _t("py_sym_none", "bilingual", path=path)
    return _t("py_sym_count", "bilingual", path=path, count=len(symbols)) + "\n" + "\n".join(symbols)


# ════════════════════════════════════════════════════════════════
# 结构化 API — 供 NativePyClient（Python 原生 ast LSP 实现）使用
#
# 返回约定：全部返回 (data, err) 二元组：
#   - data: dict / list / None（None 表示无结果，err 为 None）
#   - err:  双语错误字符串；None 表示成功
# 零依赖、零外部服务器，与上方 exec_* 同一套纯 ast 逻辑。
# ════════════════════════════════════════════════════════════════

def _fmt_params(node) -> str:
    """结构化版参数列表（与 exec_py_symbols 内部逻辑等价，独立实现避免耦合）。"""
    parts = []
    for arg in node.args.args:
        s = arg.arg
        if arg.annotation:
            if isinstance(arg.annotation, ast.Name):
                s += f": {arg.annotation.id}"
            elif isinstance(arg.annotation, ast.Subscript):
                s += ": […]"
            else:
                s += ": ?"
        parts.append(s)
        if len(parts) >= 6:
            parts.append("…")
            break
    if node.args.vararg:
        parts.append(f"*{node.args.vararg.arg}")
    if node.args.kwonlyargs:
        for a in node.args.kwonlyargs:
            parts.append(f"{a.arg}=…")
            if len(parts) >= 8:
                break
    if node.args.kwarg:
        parts.append(f"**{node.args.kwarg.arg}")
    return ", ".join(parts)


def _fmt_return(node) -> str:
    """结构化版返回类型（与 exec_py_symbols 内部逻辑等价）。"""
    ann = getattr(node, "returns", None)
    if ann is None:
        return ""
    if isinstance(ann, ast.Name):
        return f" -> {ann.id}"
    if isinstance(ann, ast.Subscript):
        return " -> […]"
    if isinstance(ann, ast.Constant):
        return f" -> {ann.value}"
    return " -> ?"


def _fmt_decorators(node) -> str:
    """结构化版装饰器（与 exec_py_symbols 内部逻辑等价）。"""
    parts = []
    for dec in getattr(node, "decorator_list", []):
        d = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(d, ast.Attribute):
            parts.append(f"@{d.attr}")
        elif isinstance(d, ast.Name):
            parts.append(f"@{d.id}")
        if len(parts) >= 2:
            break
    return " ".join(parts) + " " if parts else ""


def _pos_in_node(node, line: int, character: int) -> bool:
    """判断 (line, character)（0 起始）是否落在 node 的源码范围内。

    ast 节点的 lineno 是 1 起始，LSP 位置是 0 起始：此处统一把入参
    line 转成 1 起始再与节点比较。
    """
    line_1b = int(line) + 1
    start_line = getattr(node, "lineno", None)
    if start_line is None:
        return False
    end_line = getattr(node, "end_lineno", start_line)
    if line_1b < start_line or line_1b > end_line:
        return False
    if line_1b == start_line and character < getattr(node, "col_offset", 0):
        return False
    if line_1b == end_line:
        end_col = getattr(node, "end_col_offset", None)
        if end_col is not None and character > end_col:
            return False
    return True


def _node_signature(node) -> str:
    """返回定义节点的签名文本（用于 hover/definition 预览）。"""
    if isinstance(node, ast.ClassDef):
        bases = []
        for b in node.bases:
            try:
                bases.append(ast.unparse(b))
            except Exception:
                bases.append("?")
        sig = f"class {node.name}"
        if bases:
            sig += "(" + ", ".join(bases) + ")"
        return sig
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        sig = f"{prefix} {node.name}({_fmt_params(node)}){_fmt_return(node)}"
        dec = _fmt_decorators(node).strip()
        return f"{dec}\n{sig}" if dec else sig
    return getattr(node, "name", "?")


def _collect_defs(tree) -> dict:
    """收集文件内所有定义：name → [定义节点…]（函数/类/赋值/导入别名）。"""
    defs: dict = {}

    def add(name, node):
        defs.setdefault(name, []).append(node)

    def walk(stmts):
        for stmt in stmts:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                add(stmt.name, stmt)
                walk(stmt.body)
            elif isinstance(stmt, ast.Assign):
                for t in stmt.targets:
                    if isinstance(t, ast.Name):
                        add(t.id, stmt)
            elif isinstance(stmt, ast.AnnAssign):
                t = stmt.target
                if isinstance(t, ast.Name):
                    add(t.id, stmt)
            elif isinstance(stmt, ast.Import):
                for a in stmt.names:
                    add(a.asname or a.name.split(".")[0], stmt)
            elif isinstance(stmt, ast.ImportFrom):
                for a in stmt.names:
                    add(a.asname or a.name, stmt)
            for attr in ("body", "orelse", "finalbody"):
                sub = getattr(stmt, attr, None)
                if isinstance(sub, list):
                    walk(sub)
            handlers = getattr(stmt, "handlers", None)
            if isinstance(handlers, list):
                for h in handlers:
                    walk(h.body)
            cases = getattr(stmt, "cases", None)
            if isinstance(cases, list):
                for c in cases:
                    walk(c.body)

    walk(tree.body)
    return defs


def _find_name_at(tree, line: int, character: int):
    """找到 (line, character) 处所在的标识符名（Name/Attribute/定义名/参数名）。

    入参 line 为 0 起始（LSP 约定），比较时转为 1 起始（ast 约定）。
    """
    line_1b = int(line) + 1
    best = None
    best_span = -1

    def in_name_range(node, start_col, name):
        return (getattr(node, "lineno", None) == line_1b
                and start_col <= character < start_col + len(name))

    def scan(node):
        nonlocal best, best_span
        # 容器节点（Module 等无 lineno）不拦截，直接深入子节点
        if getattr(node, "lineno", None) is not None and not _pos_in_node(node, line_1b - 1, character):
            return
        # 定义名（FunctionDef/ClassDef 的 name 不是 Name 节点，需单独处理）
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if isinstance(node, ast.ClassDef):
                start_col = node.col_offset + len("class ")
            else:
                start_col = node.col_offset + (len("async def ") if isinstance(node, ast.AsyncFunctionDef) else len("def "))
            if in_name_range(node, start_col, node.name):
                span = (getattr(node, "end_lineno", 0) or 0) - node.lineno
                if span > best_span:
                    best_span = span
                    best = node
                    return  # 定义名优先，不再深入
        # 参数名（ast.arg）
        if isinstance(node, ast.arg):
            if in_name_range(node, getattr(node, "col_offset", -1), node.arg):
                span = 0
                if span > best_span:
                    best_span = span
                    best = node
                    return
        if isinstance(node, (ast.Name, ast.Attribute)):
            span = getattr(node, "end_lineno", 0) - getattr(node, "lineno", 0)
            if span > best_span:
                best_span = span
                best = node
        for child in ast.iter_child_nodes(node):
            scan(child)

    scan(tree)
    if best is None:
        return None
    if isinstance(best, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return best.name
    if isinstance(best, ast.arg):
        return best.arg
    if isinstance(best, ast.Name):
        return best.id
    if isinstance(best, ast.Attribute):
        return best.attr
    return None


def _loc_from_node(node, path: str) -> dict:
    """从 AST 节点构造 LSP 位置 dict（行 1 起始，与 LspLocation 对齐）。"""
    start = getattr(node, "lineno", 1)
    end = getattr(node, "end_lineno", start)
    return {
        "path": path,
        "line": start,
        "character": getattr(node, "col_offset", 0),
        "end_line": end,
        "end_character": getattr(node, "end_col_offset", 0),
        "preview": _node_signature(node),
    }


def py_symbols_structured(path: str) -> tuple:
    """结构化符号表：返回 ([{name,kind,line,character,end_line}], err)。"""
    source, err = read_py_source(path)
    if err:
        return None, err
    tree, err = parse_py_source(source, path)
    if err:
        return None, err
    symbols = []

    def visit(node, depth=0):
        if isinstance(node, ast.ClassDef):
            symbols.append({
                "name": node.name, "kind": "class",
                "line": node.lineno, "character": node.col_offset,
                "end_line": _node_end(node),
            })
            _walk(node.body, depth + 1)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = node.name
            if isinstance(node, ast.AsyncFunctionDef):
                name = "async " + node.name
            symbols.append({
                "name": name, "kind": "function",
                "line": node.lineno, "character": node.col_offset,
                "end_line": _node_end(node),
            })
            _walk(node.body, depth + 1)

    def _walk(stmts, depth):
        for stmt in stmts:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                visit(stmt, depth)
                continue
            for attr in ("body", "orelse", "finalbody"):
                sub = getattr(stmt, attr, None)
                if isinstance(sub, list):
                    _walk(sub, depth)
            handlers = getattr(stmt, "handlers", None)
            if isinstance(handlers, list):
                for h in handlers:
                    _walk(h.body, depth)
            cases = getattr(stmt, "cases", None)
            if isinstance(cases, list):
                for c in cases:
                    _walk(c.body, depth)

    _walk(tree.body, 0)
    return symbols, None


def py_diagnostics_structured(path: str) -> tuple:
    """结构化诊断：返回 ([{path,line,character,severity,message}], err)。"""
    source, err = read_py_source(path)
    if err:
        return None, err
    if not source.strip():
        return [], None
    try:
        compile(source, path, "exec")
        return [], None
    except SyntaxError as e:
        return [{
            "path": path,
            "line": getattr(e, "lineno", 1) or 1,
            "character": getattr(e, "offset", 0) or 0,
            "severity": "error",
            "message": f"{getattr(e, 'msg', 'syntax error')} (line {getattr(e, 'lineno', '?')})",
        }], None
    except (ValueError, TypeError) as e:
        return [{
            "path": path, "line": 1, "character": 0,
            "severity": "error", "message": str(e),
        }], None


def py_hover_structured(path: str, line: int, character: int) -> tuple:
    """结构化悬停：返回 ({content, language}, err)。

    优先返回位置处标识符的定义签名；否则返回所在函数/类签名。
    """
    source, err = read_py_source(path)
    if err:
        return None, err
    tree, err = parse_py_source(source, path)
    if err:
        return None, err

    name = _find_name_at(tree, int(line), int(character))
    if name:
        defs = _collect_defs(tree)
        nodes = defs.get(name)
        if nodes:
            sig = _node_signature(nodes[0])
            loc = _loc_from_node(nodes[0], path)
            return {
                "content": f"### `{sig}`\n\n→ {path}:{loc['line']}",
                "language": "python",
            }, None

    # 找不到标识符定义 → 返回所在定义（函数/类）签名
    best = None

    def scan(node):
        nonlocal best
        if not _pos_in_node(node, int(line), int(character)):
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if best is None or getattr(node, "lineno", 0) >= getattr(best, "lineno", 0):
                best = node
        for child in ast.iter_child_nodes(node):
            scan(child)

    scan(tree)
    if best is None:
        return None, None
    return {
        "content": f"### `{_node_signature(best)}`\n\n→ {path}:{getattr(best, 'lineno', 1)}",
        "language": "python",
    }, None


def py_definition_structured(path: str, line: int, character: int) -> tuple:
    """结构化跳转定义：返回 ([LspLocation-dict], err)。

    本文件内符号解析（同文件跨作用域）。跨文件 import 不做（避免误报）。
    """
    source, err = read_py_source(path)
    if err:
        return None, err
    tree, err = parse_py_source(source, path)
    if err:
        return None, err
    name = _find_name_at(tree, int(line), int(character))
    if not name:
        return [], None
    defs = _collect_defs(tree)
    nodes = defs.get(name, [])
    return [_loc_from_node(n, path) for n in nodes], None


def py_references_structured(path: str, line: int, character: int) -> tuple:
    """结构化查找引用：返回所有同名 Name 的位置（含定义处，includeDeclaration=True 对齐）。

    定义名（FunctionDef/ClassDef 的 name 不是 Name 节点）单独收集，
    保证从定义处或引用处发起都能看到完整引用列表。
    """
    source, err = read_py_source(path)
    if err:
        return None, err
    tree, err = parse_py_source(source, path)
    if err:
        return None, err
    name = _find_name_at(tree, int(line), int(character))
    if not name:
        return [], None
    locations = []

    def scan(node):
        # 定义名：函数/类名
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == name:
                locations.append(_loc_from_node(node, path))
        # 参数名
        if isinstance(node, ast.arg) and node.arg == name:
            locations.append(_loc_from_node(node, path))
        # 普通引用
        if isinstance(node, ast.Name) and node.id == name:
            locations.append(_loc_from_node(node, path))
        if isinstance(node, ast.Attribute) and node.attr == name:
            locations.append(_loc_from_node(node, path))
        for child in ast.iter_child_nodes(node):
            scan(child)

    scan(tree)
    # 去重（按 行:字符）
    seen = set()
    uniq = []
    for loc in locations:
        key = (loc["line"], loc["character"])
        if key not in seen:
            seen.add(key)
            uniq.append(loc)
    return uniq, None


def py_completion_structured(path: str, line: int, character: int) -> tuple:
    """结构化代码补全：返回 ([{label,kind,detail}], err)。

    基于当前文件作用域收集可用名字：
      - 本文件定义的函数/类/变量（含 import 别名）
      - Python 关键字（约 30 个常用）
      - 常用内置（dir(__builtins__) 截断）
    返回全部候选，由调用方截断/过滤。
    """
    source, err = read_py_source(path)
    if err:
        return None, err
    tree, err = parse_py_source(source, path)
    if err:
        return None, err

    items = []
    seen = set()

    def add(label, kind, detail=""):
        if label in seen:
            return
        seen.add(label)
        items.append({"label": label, "kind": kind, "detail": detail})

    # 1. 本文件定义
    defs = _collect_defs(tree)
    for name, nodes in defs.items():
        if isinstance(nodes[0], (ast.FunctionDef, ast.AsyncFunctionDef)):
            add(name, "function", _node_signature(nodes[0]))
        elif isinstance(nodes[0], ast.ClassDef):
            add(name, "class", _node_signature(nodes[0]))
        else:
            add(name, "variable")

    # 2. 常用关键字
    for kw in ("def", "class", "return", "if", "elif", "else", "for", "while",
               "import", "from", "as", "try", "except", "finally", "with",
               "lambda", "yield", "async", "await", "pass", "break", "continue",
               "raise", "assert", "del", "global", "nonlocal", "not", "and",
               "or", "in", "is", "None", "True", "False"):
        add(kw, "keyword")

    # 3. 常用内置函数（按字母序前 60）
    try:
        builtins = sorted(n for n in dir(__builtins__) if n.isidentifier())
    except Exception:
        builtins = []
    for b in builtins[:60]:
        add(b, "builtin")

    return items, None
