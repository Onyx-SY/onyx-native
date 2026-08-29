# -*- coding: utf-8 -*-
"""Static scan: undefined names + from-import targets missing in module.
Usage: python check_undefined.py
"""
import ast
import builtins
import os
import sys

BUILTINS = set(dir(builtins)) | {"__file__", "__builtins__"}
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "onyx", "bin")


def _arg_names(a):
    names = set()
    for arg in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs):
        names.add(arg.arg)
    if a.vararg:
        names.add(a.vararg.arg)
    if a.kwarg:
        names.add(a.kwarg.arg)
    return names


def _target_names(node):
    names = set()
    if isinstance(node, ast.Name):
        names.add(node.id)
    elif isinstance(node, (ast.Tuple, ast.List)):
        for elt in node.elts:
            names |= _target_names(elt)
    elif isinstance(node, ast.Starred):
        names |= _target_names(node.value)
    elif isinstance(node, ast.Attribute):
        names.add(node.attr)
    return names


def collect_bindings(tree):
    bound = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            bound.add(node.name)
            bound |= _arg_names(node.args)
        elif isinstance(node, ast.Lambda):
            bound |= _arg_names(node.args)
        elif isinstance(node, ast.ClassDef):
            bound.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for al in node.names:
                bound.add(al.asname or al.name.split(".")[0])
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                bound |= _target_names(t)
        elif isinstance(node, ast.AnnAssign):
            if node.target:
                bound |= _target_names(node.target)
        elif isinstance(node, ast.AugAssign):
            bound |= _target_names(node.target)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            bound |= _target_names(node.target)
        elif isinstance(node, ast.comprehension):
            bound |= _target_names(node.target)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars:
                    bound |= _target_names(item.optional_vars)
        elif isinstance(node, ast.ExceptHandler):
            if node.name:
                bound.add(node.name)
        elif isinstance(node, ast.Global):
            bound |= set(node.names)
        elif isinstance(node, ast.Nonlocal):
            bound |= set(node.names)
    return bound


def undefined_names(tree):
    bound = collect_bindings(tree)
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id not in bound and node.id not in BUILTINS:
                hits.append((node.lineno, node.id))
    return sorted(set(hits))


def has_star_import(tree):
    return any(isinstance(n, ast.ImportFrom) and any(
        al.name == "*" for al in n.names) for n in ast.walk(tree))


def top_level_defs(path):
    with open(path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), path)
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                names |= _target_names(t)
        elif isinstance(node, ast.ImportFrom):
            for al in node.names:
                names.add(al.name)
    return names


def resolve_module(imp_from_node, filepath):
    """Resolve (possibly relative) import to an absolute module file path."""
    d = os.path.dirname(filepath)
    if imp_from_node.level:
        for _ in range(imp_from_node.level - 1):
            d = os.path.dirname(d)
    mod = imp_from_node.module or ""
    parts = mod.split(".") if mod else []
    cands = []
    base = d
    for i in range(len(parts), -1, -1):
        p = os.path.join(base, *parts[:i])
        cands.append(p + ".py")
        cands.append(os.path.join(p, "__init__.py"))
        base = os.path.dirname(base)
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


def check_from_imports(filepath, tree, problems):
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level == 0:
            continue  # absolute import; interpreter resolves it
        target = resolve_module(node, filepath)
        if target is None:
            continue
        defs = top_level_defs(target)
        for al in node.names:
            if al.name == "*":
                continue
            if al.name not in defs:
                problems.append((filepath, node.lineno, al.name, target))


def main():
    files = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if fn.endswith(".py") and not fn.endswith(".bak"):
                files.append(os.path.join(dirpath, fn))
    files.sort()

    print("=" * 60)
    print("PASS 1: undefined names (NameError risk)")
    print("=" * 60)
    total = 0
    for fp in files:
        with open(fp, "r", encoding="utf-8") as f:
            src = f.read()
        try:
            tree = ast.parse(src, fp)
        except SyntaxError as e:
            print(f"  SYNTAX ERROR {fp}: {e}")
            continue
        hits = undefined_names(tree)
        star = has_star_import(tree)
        if hits:
            total += len(hits)
            tag = "  [star-import file, may be false positive]" if star else ""
            print(f"  {os.path.relpath(fp, ROOT)}{tag}")
            for lineno, name in hits:
                print(f"      L{lineno}: {name}")
    if total == 0:
        print("  (none)")

    print()
    print("=" * 60)
    print("PASS 2: from-import names missing in target module")
    print("=" * 60)
    total2 = 0
    for fp in files:
        with open(fp, "r", encoding="utf-8") as f:
            src = f.read()
        try:
            tree = ast.parse(src, fp)
        except SyntaxError:
            continue
        problems = []
        check_from_imports(fp, tree, problems)
        for pfp, lineno, name, target in problems:
            total2 += 1
            print(f"  {os.path.relpath(pfp, ROOT)} L{lineno}: "
                  f"'{name}' not defined in {os.path.relpath(target, ROOT)}")
    if total2 == 0:
        print("  (none)")

    print()
    print(f"TOTAL: {total} undefined-name sites, {total2} broken from-imports")
    return 1 if (total + total2) else 0


if __name__ == "__main__":
    sys.exit(main())
