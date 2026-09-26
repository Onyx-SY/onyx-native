# -*- coding: utf-8 -*-
"""审计 2：参数命名风格 / 一致性（camelCase、'-' 前缀、同义不同名）。"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bin.ai_lib import native_tools as nt  # noqa: E402

tools = nt.build_native_tools()
schemas = {}
for t in tools:
    fn = t.get("function", t)
    schemas[fn.get("name")] = set((fn.get("parameters", {}) or {}).get("properties", {}) or {})

print("=== 1. camelCase 参数（与 snake_case 主流不一致）===")
for n, props in sorted(schemas.items()):
    camel = [p for p in props if re.search(r"[a-z][A-Z]", p)]
    if camel:
        print(f"  {n}: {camel}")

print("\n=== 2. 以 '-' 开头或非常规命名的参数 ===")
for n, props in sorted(schemas.items()):
    odd = [p for p in props if p.startswith("-")]
    if odd:
        print(f"  {n}: {odd}")

print("\n=== 3. 同类工具的参数名差异（路径 / 文本）===")
for n, props in sorted(schemas.items()):
    if "path" in props or "file_path" in props:
        pname = "path" if "path" in props else "file_path"
        print(f"  {n}: 路径参数 = {pname}")

print("\n=== 4. 会话标识参数（session_id / uuid / path）===")
for n, props in sorted(schemas.items()):
    ids = [p for p in props if p in ("session_id", "uuid", "chat_id")]
    if ids:
        print(f"  {n}: {ids}")

print("\n=== 5. 旧文本参数命名 ===")
for n, props in sorted(schemas.items()):
    t = [p for p in props if p in ("old_string", "search", "old_text")]
    if t:
        print(f"  {n}: {t} / 新文本 = {[p for p in props if p in ('new_string','replace','new_text')]}")
