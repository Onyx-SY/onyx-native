"""
markup_parser.py — Onyx 自研文件编辑标记语言解析器

从 AI 回复文本中提取以下标记块：

查看类（精确，不截断）:
  [VIEW:path]                     → 完整文件带行号
  [VIEW:path:10-30]               → 第 10 到 30 行
  [VIEW:path:42]                  → 第 42 行
  [VIEW:path:search:关键词]        → 搜索含关键词的行

编辑类:
  [EDIT:path]\n<<<<< SEARCH\n旧\n=====\n新\n>>>>> REPLACE  → SEARCH/REPLACE 替换

  [WRITE:path]\n内容\n[WRITE:DONE]   → 覆盖写入

  [APPEND:path]\n内容                 → 文件末尾追加

  [INSERT:path:42]\n内容\n[INSERT:DONE]  → 第 42 行后插入

  [DELETE:path:10-15]                    → 按行号删除
  [DELETE:path:search:内容]               → 按内容搜索删除
  [DELETE:path:10-15:show]               → 删除并展示被删内容
"""

import re
from typing import List, Dict, Optional


def parse_markup(text: str) -> List[Dict]:
    """
    从文本中提取所有标记块。
    使用 [^:] 确保冒号是字段分隔符，不会吞掉参数部分。
    """
    if not text:
        return []

    blocks = []
    lines = text.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # ── VIEW 块 ──
        # [VIEW:path]  [VIEW:path:10-30]  [VIEW:path:42]  [VIEW:path:search:关键词]
        view_match = re.match(
            r'^\[VIEW:([^:]+)(?::([^:]+))?(?::(.+))?\]$', stripped
        )
        if view_match:
            path = view_match.group(1)
            arg1 = view_match.group(2)   # "10-30"、"42"、"search"
            arg2 = view_match.group(3)   # search 关键词

            block = {"type": "view", "path": path}

            if arg1 is None:
                pass  # 完整文件
            elif arg1 == "search" and arg2:
                block["search"] = arg2
            elif arg1.isdigit():
                block["line"] = int(arg1)
            elif re.match(r'^\d+-\d+$', arg1):
                start_s, end_s = arg1.split("-", 1)
                block["start"] = int(start_s)
                block["end"] = int(end_s)
            else:
                block["search"] = arg1

            blocks.append(block)
            i += 1
            continue

        # ── EDIT 块（行号范围模式）──
        # [EDIT:path:10-20]\n新内容\n[EDIT:DONE]
        # 直接替换第 10-20 行，无需提供旧内容
        edit_range_match = re.match(r'^\[EDIT:([^:]+):(\d+)-(\d+)\]$', stripped)
        if edit_range_match:
            path = edit_range_match.group(1)
            start_line = int(edit_range_match.group(2))
            end_line = int(edit_range_match.group(3))
            i += 1
            content_lines = []
            while i < len(lines) and lines[i].strip() != "[EDIT:DONE]":
                content_lines.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1  # 跳过 [EDIT:DONE]
            blocks.append({
                "type": "edit_range",
                "path": path,
                "start": start_line,
                "end": end_line,
                "content": "\n".join(content_lines),
            })
            continue

        # ── EDIT 块（SEARCH/REPLACE 模式）──
        # [EDIT:path]\n<<<<<<< SEARCH\n旧\n=======\n新\n>>>>>>> REPLACE
        edit_match = re.match(r'^\[EDIT:([^:]+)\]$', stripped)
        if edit_match:
            path = edit_match.group(1)
            i += 1
            # 跳过空行
            while i < len(lines) and not lines[i].strip():
                i += 1

            if i < len(lines) and lines[i].strip() == "<<<<<<< SEARCH":
                i += 1
                search_lines = []
                while i < len(lines) and lines[i].strip() != "=======":
                    search_lines.append(lines[i])
                    i += 1
                if i < len(lines) and lines[i].strip() == "=======":
                    i += 1
                    replace_lines = []
                    while i < len(lines) and lines[i].strip() != ">>>>>>> REPLACE":
                        replace_lines.append(lines[i])
                        i += 1
                    if i < len(lines) and lines[i].strip() == ">>>>>>> REPLACE":
                        blocks.append({
                            "type": "edit",
                            "path": path,
                            "search": "\n".join(search_lines),
                            "replace": "\n".join(replace_lines),
                        })
                        i += 1
                        continue

            # 格式不对 → 回退
            blocks.append({"type": "edit", "path": path,
                           "search": "", "replace": "",
                           "_error": "invalid EDIT format"})
            continue

        # ── WRITE 块 ──
        # [WRITE:path]\n内容\n[WRITE:DONE]
        write_match = re.match(r'^\[WRITE:([^:]+)\]$', stripped)
        if write_match:
            path = write_match.group(1)
            i += 1
            content_lines = []
            while i < len(lines) and lines[i].strip() != "[WRITE:DONE]":
                content_lines.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1  # 跳过 [WRITE:DONE]
            blocks.append({
                "type": "write",
                "path": path,
                "content": "\n".join(content_lines),
            })
            continue

        # ── APPEND 块 ──
        # [APPEND:path]\n内容（无结束标记，到下一个标记或文件尾）
        append_match = re.match(r'^\[APPEND:([^:]+)\]$', stripped)
        if append_match:
            path = append_match.group(1)
            i += 1
            content_lines = []
            while i < len(lines) and not lines[i].strip().startswith("["):
                content_lines.append(lines[i])
                i += 1
            blocks.append({
                "type": "append",
                "path": path,
                "content": "\n".join(content_lines).rstrip("\n"),
            })
            continue

        # ── INSERT 块 ──
        # [INSERT:path:行号]\n内容\n[INSERT:DONE]
        insert_match = re.match(r'^\[INSERT:([^:]+):(\d+)\]$', stripped)
        if insert_match:
            path = insert_match.group(1)
            line_no = int(insert_match.group(2))
            i += 1
            content_lines = []
            while i < len(lines) and lines[i].strip() != "[INSERT:DONE]":
                content_lines.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1  # 跳过 [INSERT:DONE]
            blocks.append({
                "type": "insert",
                "path": path,
                "line": line_no,
                "content": "\n".join(content_lines),
            })
            continue

        # ── DELETE 块 ──
        # [DELETE:path:10-15]         — 按行号删除
        # [DELETE:path:10-15:show]    — 删除并展示
        # [DELETE:path:search:内容]   — 按内容删除
        delete_match = re.match(r'^\[DELETE:([^:]+):(.+)\]$', stripped)
        if delete_match:
            path = delete_match.group(1)
            rest = delete_match.group(2)

            # [DELETE:path:10-15:show]
            show_del = re.match(r'^(\d+-\d+):show$', rest)
            if show_del:
                start_s, end_s = show_del.group(1).split("-", 1)
                blocks.append({
                    "type": "delete",
                    "path": path,
                    "start": int(start_s),
                    "end": int(end_s),
                    "show": True,
                })
                i += 1
                continue

            # [DELETE:path:10-15]
            range_match = re.match(r'^(\d+)-(\d+)$', rest)
            if range_match:
                blocks.append({
                    "type": "delete",
                    "path": path,
                    "start": int(range_match.group(1)),
                    "end": int(range_match.group(2)),
                    "show": False,
                })
                i += 1
                continue

            # [DELETE:path:search:内容]
            search_del = re.match(r'^search:(.+)$', rest)
            if search_del:
                blocks.append({
                    "type": "delete_by_content",
                    "path": path,
                    "search": search_del.group(1),
                })
                i += 1
                continue

            # 格式不识别
            i += 1
            continue

        # ── REPLACE_ALL 块 ──
        # [REPLACE_ALL:glob_pattern]\n旧内容\n=====\n新内容\n[REPLACE_ALL:DONE]
        replace_all_match = re.match(r'^\[REPLACE_ALL:([^\]]+)\]$', stripped)
        if replace_all_match:
            glob_pattern = replace_all_match.group(1)
            i += 1
            search_lines = []
            replace_lines = []
            mode = "search"  # 先收集 search，遇到 ===== 切到 replace
            while i < len(lines) and lines[i].strip() != "[REPLACE_ALL:DONE]":
                line = lines[i]
                if line.strip() == "=====" and mode == "search":
                    mode = "replace"
                elif mode == "search":
                    search_lines.append(line)
                else:
                    replace_lines.append(line)
                i += 1
            if i < len(lines):
                i += 1  # 跳过 [REPLACE_ALL:DONE]
            blocks.append({
                "type": "replace_all",
                "glob": glob_pattern,
                "search": "\n".join(search_lines),
                "replace": "\n".join(replace_lines),
            })
            continue

        # ── BATCH 块（原子批量操作）──
        # [BATCH]\n多个 [EDIT:]/[WRITE:]/[DELETE:] 等\n[BATCH:DONE]
        batch_match = re.match(r'^\[BATCH\]$', stripped)
        if batch_match:
            i += 1
            inner_lines = []
            while i < len(lines) and lines[i].strip() != "[BATCH:DONE]":
                inner_lines.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1  # 跳过 [BATCH:DONE]
            inner_text = "\n".join(inner_lines)
            # 递归解析内部标记块
            inner_blocks = parse_markup(inner_text)
            blocks.append({
                "type": "batch",
                "blocks": inner_blocks,
                "source": inner_text,
            })
            continue

        # ── 普通行，跳过 ──
        i += 1

    return blocks


def has_markup(text: str) -> bool:
    """快速检查文本是否包含任何标记块（正则匹配）"""
    if not text:
        return False
    patterns = [
        r'\[VIEW:',
        r'\[EDIT:',
        r'\[WRITE:',
        r'\[APPEND:',
        r'\[INSERT:',
        r'\[DELETE:',
        r'\[REPLACE_ALL:',
        r'\[BATCH\]',
    ]
    for p in patterns:
        if re.search(p, text):
            return True
    return False


def extract_blocks_by_type(blocks: List[Dict], block_type: str) -> List[Dict]:
    """按类型过滤标记块"""
    return [b for b in blocks if b.get("type") == block_type]
