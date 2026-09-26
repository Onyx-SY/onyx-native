# -*- coding: utf-8 -*-
"""把 Textual SVG 截图按行 dump 成「文本 + 颜色」，用于核对 TUI 真的分色显示。
用法: python3 tmp/svg_colors.py tmp/shot_tools.txt.svg
"""
import os
import re
import sys
import xml.etree.ElementTree as ET

svg_path = sys.argv[1] if len(sys.argv) > 1 else "tmp/shot_tools.txt.svg"
svg = open(svg_path, encoding="utf-8").read()

class_fill = {}
for m in re.finditer(r"\.([\w-]+)\s*\{([^}]*)\}", svg):
    fm = re.search(r"fill:\s*(#[0-9a-fA-F]{3,8})", m.group(2))
    if fm:
        class_fill[m.group(1)] = fm.group(1).lower()

NS = "{http://www.w3.org/2000/svg}"
rows = {}
for t in ET.iterparse(svg_path, events=("end",)):
    el = t[1]
    if el.tag != NS + "text":
        continue
    if "title" in (el.get("class") or ""):
        continue
    try:
        y = float(el.get("y") or 0)
        txt = "".join(el.itertext())
    except Exception:
        continue
    fill = el.get("fill") or class_fill.get((el.get("class") or "").strip(), "?")
    rows.setdefault(round(y), []).append((txt, fill))

for y in sorted(rows):
    parts = [f"{s}[{f}]" for s, f in rows[y] if s.strip()]
    if not parts:
        continue
    print(f"y={y:>6}  " + "  ".join(parts))
