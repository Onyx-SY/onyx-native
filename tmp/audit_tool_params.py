# -*- coding: utf-8 -*-
"""审计：原生工具 schema 参数 vs 调度层实际读取的参数。"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bin.ai_lib import native_tools as nt  # noqa: E402

tools = nt.build_native_tools()
print(f"共 {len(tools)} 个原生工具\n")

schemas = {}
for t in tools:
    fn = t.get("function", t)
    name = fn.get("name")
    params = fn.get("parameters", {}) or {}
    props = params.get("properties", {}) or {}
    schemas[name] = set(props.keys())

src = open(os.path.join(ROOT, "bin", "ai_lib", "mcp_exec.py"), encoding="utf-8").read()
m = re.search(r"_BUILTIN_HANDLERS\s*=\s*\{(.*?)\n    \}", src, re.S)
block = m.group(1) if m else ""
entries = re.split(r'\n\s{8}"([A-Za-z_][A-Za-z0-9_]*)":\s', "\n" + block)
handlers = {}
for i in range(1, len(entries) - 1, 2):
    handlers[entries[i]] = set(re.findall(r'p\.get\(\s*"([^"]+)"', entries[i + 1]))

print("=== A. schema 声明了、但调度层从未读取的参数（可能被静默忽略）===")
found = False
for name, props in schemas.items():
    if name not in handlers:
        continue
    unused = props - handlers[name]
    if unused:
        found = True
        print(f"  {name}: {sorted(unused)}")
if not found:
    print("  （无）")

print("\n=== B. 调度层读取了、但 schema 未声明的参数（隐藏参数）===")
found = False
for name, used in handlers.items():
    if name not in schemas:
        print(f"  {name}: 调度层存在但无 schema（{sorted(used)}）")
        continue
    extra = used - schemas[name]
    if extra:
        found = True
        print(f"  {name}: {sorted(extra)}")
if not found:
    print("  （无）")

print("\n=== C. 有 schema 但无调度处理器 ===")
missing = [n for n in schemas if n not in handlers]
print("  " + (", ".join(sorted(missing)) if missing else "（无）"))

print("\n=== D. 全部工具参数一览 ===")
for name in sorted(schemas):
    print(f"  {name}: {sorted(schemas[name])}")
