# -*- coding: utf-8 -*-
"""step-7 验证（双语审计）：扫描 AI 界面代码里残留的硬编码中文串。

- 只报「字符串常量」里的中文，自动排除注释与 docstring；
- 输出文件名 + 行号 + 片段，便于人工确认是否还需要走 _t()。

用法：python3 tmp/i18n_key_check.py
"""
import ast
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CJK = re.compile(r"[\u4e00-\u9fff]")

# 已知「数据表」性质、本来就分中英两份的文件/常量，跳过
SKIP_NAMES = {
    "_SLASH_COMMANDS_CN", "_HELP_TEXT_CN",   # ai_interactive.py：本身就是中文表
}


def docstrings_of(tree):
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            d = ast.get_docstring(node, clean=False)
            if d is not None:
                out.add(d)
    return out


def scan(path):
    src = io.open(path, encoding="utf-8").read()
    lines = src.splitlines()
    tree = ast.parse(src)
    docs = docstrings_of(tree)
    # 收集「赋值给跳过名单」的字符串
    skipped = set()
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        for t in targets:
            if isinstance(t, ast.Name) and t.id in SKIP_NAMES:
                for sub in ast.walk(node.value):
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                        skipped.add(sub.value)
    # 同一行出现「else + 非中文串」→ 视为内联双语
    def inline_bilingual(lineno):
        line = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
        if " else " not in line:
            return False
        return bool(re.search(r"['\"][^'\"]*[A-Za-z][^'\"]*['\"]", line))

    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            if v in docs or v in skipped or not CJK.search(v):
                continue
            hits.append((node.lineno, v, inline_bilingual(node.lineno)))
    return sorted(hits)


def main():
    targets = ["bin/ai_tui.py", "bin/ai_interactive.py"]
    for rel in targets:
        path = os.path.join(ROOT, rel)
        hits = scan(path)
        only_cn = [h for h in hits if not h[2]]
        print(f"=== {rel}：含中文的字符串常量 {len(hits)} 处，"
              f"其中疑似「仅中文、无英文分支」{len(only_cn)} 处")
        for line, v, bi in only_cn:
            print(f"  L{line}: {v[:100]!r}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
