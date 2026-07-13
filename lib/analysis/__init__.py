"""
lib/analysis — 代码理解引擎

对标 Claude Code 的 AST 级跨文件引用追踪。

用法:
  from lib.analysis import analyze_symbol, file_outline, search_references

  # 查找符号定义 + 所有引用
  result = analyze_symbol("handle_ai", root_dir="bin")
  # 返回: {"definitions": [...], "references": [...], "files_affected": [...]}

  # 文件大纲
  print(file_outline("bin/ai_cmd.py"))

  # 跨文件搜索引用
  refs = search_references("handle_ai", root_dir="bin")
"""

import os
import sys
from typing import List, Optional, Dict, Any

from .ts_queries import (
    get_symbols, find_definition, find_references,
    outline as _outline, language_for_path, strip_comments,
)


# ──────────────────────────── 核心 API ────────────────────────────

def file_outline(file_path: str) -> str:
    """返回文件的符号大纲。"""
    if not os.path.exists(file_path):
        return f"❌ File not found: {file_path}"
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            code = f.read()
    except Exception as e:
        return f"❌ Read failed: {e}"
    lang = language_for_path(file_path)
    if not lang:
        return f"⚠️  Unsupported language: {file_path}"
    symbols = get_symbols(code, lang)
    if not symbols:
        return "(no symbols found)"
    lines = [f"📄 {file_path}  ({len(symbols)} symbols)"]
    for s in symbols:
        kind_pad = s["kind"].ljust(14)
        lines.append(f"  L{s['line']:4d}  {kind_pad} {s['name']}")
    return "\n".join(lines)


def analyze_symbol(name: str, root_dir: str = ".") -> Dict[str, Any]:
    """
    跨文件追踪符号。

    返回:
      {
        "name": str,
        "definitions": [{"file": str, "line": int, "kind": str}, ...],
        "references": [{"file": str, "line": int}, ...],
        "files_affected": [str, ...],
      }
    """
    result = {
        "name": name,
        "definitions": [],
        "references": [],
        "files_affected": [],
    }
    seen_files = set()

    for dirpath, _, filenames in os.walk(root_dir):
        # 跳过依赖/缓存目录
        skip_dirs = {"node_modules", "__pycache__", ".git", ".venv",
                     "target", "dist", "build", ".next", ".turbo"}
        if any(skip in dirpath.split(os.sep) for skip in skip_dirs):
            continue
        for fn in filenames:
            fpath = os.path.join(dirpath, fn)
            lang = language_for_path(fpath)
            if not lang:
                continue
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    code = f.read()
            except Exception:
                continue

            # 定义
            defn = find_definition(code, name, lang)
            if defn:
                result["definitions"].append({
                    "file": fpath,
                    "line": defn["line"],
                    "kind": defn["kind"],
                })
                seen_files.add(fpath)

            # 引用
            refs = find_references(code, name, lang)
            if refs:
                for r in refs:
                    result["references"].append({
                        "file": fpath,
                        "line": r["line"],
                    })
                seen_files.add(fpath)

    result["files_affected"] = sorted(seen_files)
    return result


def search_references(name: str, root_dir: str = ".") -> List[Dict[str, Any]]:
    """跨文件搜索符号引用（轻量版，仅引用不包含定义）。"""
    result = analyze_symbol(name, root_dir)
    return result["references"]


def project_summary(root_dir: str = ".", max_files: int = 30) -> str:
    """生成项目结构摘要（文件名 + 顶层符号）。"""
    lines = [f"📁 Project: {os.path.abspath(root_dir)}"]
    count = 0
    for dirpath, _, filenames in os.walk(root_dir):
        skip_dirs = {"node_modules", "__pycache__", ".git", ".venv",
                     "target", "dist", "build", ".next", ".turbo"}
        if any(skip in dirpath.split(os.sep) for skip in skip_dirs):
            continue
        for fn in filenames:
            if count >= max_files:
                break
            fpath = os.path.join(dirpath, fn)
            lang = language_for_path(fpath)
            if not lang:
                continue
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    code = f.read()
            except Exception:
                continue
            symbols = get_symbols(code, lang)
            rel = os.path.relpath(fpath, root_dir)
            if symbols:
                names = ", ".join(s["name"] for s in symbols[:5])
                lines.append(f"  📄 {rel}  →  {names}")
                if len(symbols) > 5:
                    lines[-1] += f" ... (+{len(symbols)-5})"
            else:
                lines.append(f"  📄 {rel}")
            count += 1
    if count == 0:
        lines.append("  (no source files found)")
    return "\n".join(lines)
