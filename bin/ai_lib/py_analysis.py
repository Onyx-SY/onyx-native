"""
Python 内置代码分析 — 零依赖，纯 ast 实现。
替代外部 LSP server（pyright/pylsp 等）的基础分析功能。
"""

import ast
import os


# ── 共享辅助函数 ──

_MAX_PY_SIZE = 1024 * 1024 * 2  # 2MB

def read_py_source(path: str) -> tuple:
    """读取 .py 文件内容，返回 (source, None) 或 (None, 错误信息)。"""
    if not os.path.isfile(path):
        return None, f"❌ 文件不存在: {path}"
    if not path.endswith(".py"):
        return None, "⚠️ 仅支持 .py 文件"
    size = os.path.getsize(path)
    if size > _MAX_PY_SIZE:
        return None, f"⚠️ 文件过大 ({size / 1024 / 1024:.1f}MB)"
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(), None
    except Exception as e:
        return None, f"❌ 读取失败: {e}"


def parse_py_source(source: str, path: str = "") -> tuple:
    """解析 Python 源码为 AST，返回 (tree, None) 或 (None, 错误信息)。"""
    try:
        return ast.parse(source, filename=path), None
    except SyntaxError as e:
        return None, f"❌ 语法错误:\n  行 {e.lineno}: {e.msg}\n  {e.text or ''}"


def _node_end(node) -> int:
    return getattr(node, 'end_lineno', node.lineno)


# ── py_diagnostics ──

def exec_py_diagnostics(path: str) -> str:
    """检查 Python 文件的语法错误和常见问题。"""
    source, err = read_py_source(path)
    if err:
        return err
    if not source.strip():
        return f"📋 {path}: 空文件"
    if not source.endswith("\n"):
        source += "\n"  # ast.parse 对末行无换行的文件也能处理，但确保安全

    tree, err = parse_py_source(source, path)
    if err:
        return err

    # 收集所有被引用的名字（不含定义站点）
    used_names = set()
    def_names = {}  # name → lineno（仅顶层函数/类）
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used_names.add(node.id)

    issues = []

    # ── 只处理 module.body 中的顶层节点，避免嵌套干扰 ──
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            def_names[node.name] = node.lineno
        elif isinstance(node, ast.ClassDef):
            def_names[node.name] = node.lineno

    # ── 未使用的导入 ──
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                if name not in used_names:
                    issues.append(f"  ⚠️ 行 {node.lineno}: 未使用的导入 `{alias.name}`")
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                local_name = alias.asname or alias.name
                if local_name not in used_names:
                    issues.append(f"  ⚠️ 行 {node.lineno}: 未使用的导入 `{alias.name}`")

    # ── 裸 except ──
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            issues.append(f"  ⚠️ 行 {node.lineno}: bare except（应指定异常类型）")

    # ── return/raise 后的不可达代码（仅函数/异步函数级别） ──
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            for i, stmt in enumerate(body):
                if isinstance(stmt, (ast.Return, ast.Raise)):
                    if i + 1 < len(body) and not isinstance(body[i + 1], ast.Pass):
                        issues.append(f"  💀 行 {body[i + 1].lineno}: "
                                      f"{type(stmt).__name__} 后存在不可达代码")

    # ── 死代码：顶层函数/类定义了但未在当前文件使用 ──
    for name, lineno in def_names.items():
        if name not in used_names:
            issues.append(f"  💤 行 {lineno}: `{name}` 已定义但未在当前文件中使用")

    if not issues:
        return f"✅ {path}: 语法正确，无明显问题"
    return f"📋 {path}: 发现 {len(issues)} 个问题\n" + "\n".join(issues)


# ── py_symbols ──

def exec_py_symbols(path: str) -> str:
    """提取 Python 文件的符号表（函数、类、方法及装饰器、类型注释）。"""
    source, err = read_py_source(path)
    if err:
        return err
    if not source.strip():
        return f"ℹ️ {path}: 空文件"

    tree, err = parse_py_source(source, path)
    if err:
        return err

    source_lines = source.split("\n")

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

    def _body_lines(node) -> int:
        start, end = node.lineno, _node_end(node)
        c = 0
        for i in range(start, end):
            line = source_lines[i - 1].strip() if i <= len(source_lines) else ""
            if line and not line.startswith("#") and not line.startswith(('"""', "'''")):
                c += 1
        return c

    symbols = []

    def _visit(node, depth=0):
        ind = "  " * depth
        if isinstance(node, ast.ClassDef):
            deco = _decorators(node)
            methods = []
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    e = _node_end(child)
                    methods.append(
                        f"  {ind}  {_decorators(child)}ƒ `{child.name}"
                        f"({_params(child)}){_return_type(child)}` "
                        f"(行 {child.lineno}-{e}, {_body_lines(child)} 行)"
                    )
            cls_end = _node_end(node)
            symbols.append(
                f"{ind}◈ {deco}class `{node.name}` "
                f"(行 {node.lineno}-{cls_end}, {_body_lines(node)} 行)"
            )
            symbols.extend(methods)
            for child in node.body:
                if isinstance(child, ast.ClassDef):
                    _visit(child, depth + 1)
        elif isinstance(node, ast.FunctionDef):
            e = _node_end(node)
            symbols.append(
                f"{ind}{_decorators(node)}ƒ def `{node.name}({_params(node)})"
                f"{_return_type(node)}` (行 {node.lineno}-{e}, {_body_lines(node)} 行)"
            )
        elif isinstance(node, ast.AsyncFunctionDef):
            e = _node_end(node)
            symbols.append(
                f"{ind}{_decorators(node)}ƒ async `{node.name}({_params(node)})"
                f"{_return_type(node)}` (行 {node.lineno}-{e}, {_body_lines(node)} 行)"
            )

    for child in tree.body:
        _visit(child)

    if not symbols:
        return f"ℹ️ {path}: 未找到符号"
    return f"📋 {path}: {len(symbols)} 个符号\n" + "\n".join(symbols)


# ── py_definition ──

def exec_py_definition(path: str, line: int, character: int) -> str:
    """查找 Python 文件中某位置的符号定义。"""
    source, err = read_py_source(path)
    if err:
        return err

    tree, err = parse_py_source(source, path)
    if err:
        return err

    source_lines = source.split("\n")
    if line < 1 or line > len(source_lines):
        return f"❌ 行号超出范围: {line}（共 {len(source_lines)} 行）"

    cursor_line = source_lines[line - 1]
    col = min(character, len(cursor_line))
    start = col
    while start > 0 and (cursor_line[start - 1].isalnum() or cursor_line[start - 1] == '_'):
        start -= 1
    end = col
    while end < len(cursor_line) and (cursor_line[end].isalnum() or cursor_line[end] == '_'):
        end += 1
    symbol_name = cursor_line[start:end]

    if not symbol_name:
        return f"ℹ️ 行 {line} 列 {character} 未找到符号"

    # 判断光标是否在某个类/函数体内（用于作用域感知）
    cursor_within_class = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            if node.lineno <= line <= _node_end(node):
                cursor_within_class = node.name

    definitions = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == symbol_name:
                preview = source_lines[node.lineno - 1].strip()[:80]
                # 如果光标在类内，优先返回同类的成员
                if cursor_within_class:
                    # 检查这个定义是否在同一个类里
                    for parent in ast.walk(tree):
                        if isinstance(parent, ast.ClassDef) and parent.name == cursor_within_class:
                            if node in parent.body:
                                definitions.insert(0, f"  📄 `{path}:{node.lineno}` — `{preview}`")
                                break
                    else:
                        definitions.append(f"  📄 `{path}:{node.lineno}` — `{preview}`")
                else:
                    definitions.append(f"  📄 `{path}:{node.lineno}` — `{preview}`")
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == symbol_name:
                    preview = source_lines[node.lineno - 1].strip()[:80]
                    definitions.append(f"  📄 `{path}:{node.lineno}` — `{preview}`")

    if not definitions:
        return f"ℹ️ `{symbol_name}` 在 {path}:{line} 未找到定义"
    return f"🎯 `{symbol_name}` 定义（共 {len(definitions)} 处）:\n" + "\n".join(definitions[:5])
