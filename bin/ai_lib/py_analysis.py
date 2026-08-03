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
