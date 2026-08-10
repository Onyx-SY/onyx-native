# -*- coding: utf-8 -*-
"""
prompt_cache.py — 统一前缀缓存管理器

将所有静态提示词内容（系统提示词、JSON 索引、FC 介绍等）合并为一个
稳定前缀文件，注入 API 请求的 messages[0]，确保 DeepSeek 前缀缓存
高命中率。

文件结构：
  .ai_s/tmp/
    .prompt        → 所有静态内容的规范文件
    1.tmp          → 上一轮的 2.tmp 副本（用于前缀命中率对比）
    2.tmp          → .prompt + 分隔线（当前轮的前缀）
    hit_rate       → 每轮的前缀命中率记录
    hit_rate_summary → 任务结束后的汇总

工作流：
  1. 会话开始时: build_prompt_file() → init_tmp_files()
  2. 每次 API 调用前: track_and_rotate() → 记录命中率 + 轮转
  3. 任务结束时: summarize_hit_rates() → 显示汇总
  4. 归档时: strip_prompt_prefix() → 剥离前缀（分隔线以上内容）
"""

import os
import json
from datetime import datetime
from typing import Tuple, List, Optional


# ── 分隔线标记 ──
PROMPT_SEPARATOR = "\n\n--- PROMPT SEPARATOR ---\n\n"


# ── 路径工具 ──

def get_tmp_dir(home_dir: str) -> str:
    """获取 .ai_s/tmp/ 目录路径（自动创建）"""
    d = os.path.join(home_dir, ".ai_s", "tmp")
    os.makedirs(d, exist_ok=True)
    return d


def _prompt_path(home_dir: str) -> str:
    return os.path.join(get_tmp_dir(home_dir), ".prompt")


def _tmp1_path(home_dir: str, session_id: str = "") -> str:
    # 按 session 分文件：并发会话（双 REPL 同 home）各自独立轮转，命中率指标不串扰
    return os.path.join(get_tmp_dir(home_dir), f"1.{session_id}.tmp" if session_id else "1.tmp")


def _tmp2_path(home_dir: str, session_id: str = "") -> str:
    return os.path.join(get_tmp_dir(home_dir), f"2.{session_id}.tmp" if session_id else "2.tmp")


def _hit_rate_dir(home_dir: str) -> str:
    """获取 .ai_s/tmp/hit_rate/ 目录路径（按 UUID 分文件）"""
    d = os.path.join(get_tmp_dir(home_dir), "hit_rate")
    os.makedirs(d, exist_ok=True)
    return d


def _hit_rate_path(home_dir: str, session_id: str = "") -> str:
    """获取会话级命中率 JSONL 文件路径"""
    return os.path.join(_hit_rate_dir(home_dir), f"{session_id}.jsonl")


def _hit_rate_log_path(home_dir: str, session_id: str = "") -> str:
    """获取会话级命中率人类可读日志路径"""
    return os.path.join(_hit_rate_dir(home_dir), f"{session_id}.log")


# ── 构建 .prompt ──

def delete_prompt_file(home_dir: str) -> None:
    """删除旧的 .prompt 文件（新会话开始时调用，避免数据累积过旧）。"""
    prompt_path = _prompt_path(home_dir)
    if os.path.exists(prompt_path):
        os.remove(prompt_path)


def build_prompt_file(
    home_dir: str,
    system_prompt: str = "",
    tools_prompt: str = "",
    hippocampus_index: str = "",
) -> str:
    """
    构建 .ai_s/tmp/.prompt 文件。

    将以下静态内容合并为一个确定性前缀：
      - 系统提示词（agreement.md）
      - AI 工具 / FC 介绍（ai_tools_prompt）
      - 海马体索引（hippocampus index）

    注意：onyx_ai.md 不在此处 — 它在 API 调用时动态追加到末尾。

    Returns:
        .prompt 文件的完整路径
    """
    tmp_dir = get_tmp_dir(home_dir)
    prompt_path = _prompt_path(home_dir)

    parts = []

    if system_prompt:
        parts.append(system_prompt.rstrip())

    if tools_prompt:
        parts.append("\n# Available Tools\n" + tools_prompt.rstrip())

    if hippocampus_index:
        parts.append("\n# Memory Index\n" + hippocampus_index.rstrip())

    content = "\n\n".join(parts).strip()

    # 原子写入
    tmp_write = prompt_path + ".tmp"
    try:
        with open(tmp_write, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_write, prompt_path)
    except Exception:
        try:
            os.remove(tmp_write)
        except Exception:
            pass
        raise

    return prompt_path


# ── 确保 tmp 目录存在 ──

def ensure_tmp_dir(home_dir: str) -> str:
    """确保 .ai_s/tmp/ 目录存在。返回目录路径。"""
    return get_tmp_dir(home_dir)


# ── 初始化 tmp 文件 ──

def init_tmp_files(home_dir: str, session_id: str = "") -> Tuple[str, str]:
    """
    初始化 1.tmp 和 2.tmp（仅首次使用）。

    1.tmp → 空文件（第一轮没有"上一轮"可比较）
    2.tmp → .prompt 内容 + 分隔线

    Args:
        session_id: 当前会话 UUID，用于按会话隔离 tmp 文件

    Returns:
        (1.tmp 路径, 2.tmp 路径)
    """
    tmp_dir = get_tmp_dir(home_dir)
    prompt_path = _prompt_path(home_dir)
    tmp1 = _tmp1_path(home_dir, session_id)
    tmp2 = _tmp2_path(home_dir, session_id)

    # 读取 .prompt 内容
    prompt_content = ""
    if os.path.exists(prompt_path):
        with open(prompt_path, "r", encoding="utf-8") as f:
            prompt_content = f.read().rstrip()

    # 构建 2.tmp = .prompt + 分隔线
    content_2 = prompt_content + PROMPT_SEPARATOR

    # 写入 2.tmp
    with open(tmp2, "w", encoding="utf-8") as f:
        f.write(content_2)

    # 写入 1.tmp（空 — 第一轮无历史）
    with open(tmp1, "w", encoding="utf-8") as f:
        f.write("")

    return tmp1, tmp2


def refresh_prompt_tmp(home_dir: str, session_id: str = "") -> str:
    """
    用最新的 .prompt 重建 2.tmp。

    与 init_tmp_files 的区别：不动 1.tmp。
    用于每次 handle_ai 调用时刷新前缀（海马体可能新增条目），
    但保留 1.tmp 作为上一轮的对照基线。

    Args:
        session_id: 当前会话 UUID，用于按会话隔离 tmp 文件

    Returns:
        2.tmp 路径
    """
    prompt_path = _prompt_path(home_dir)
    tmp2 = _tmp2_path(home_dir, session_id)

    prompt_content = ""
    if os.path.exists(prompt_path):
        with open(prompt_path, "r", encoding="utf-8") as f:
            prompt_content = f.read().rstrip()

    content_2 = prompt_content + PROMPT_SEPARATOR

    # 原子写入 2.tmp
    tmp_write = tmp2 + ".tmp"
    try:
        with open(tmp_write, "w", encoding="utf-8") as f:
            f.write(content_2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_write, tmp2)
    except Exception:
        try:
            os.remove(tmp_write)
        except Exception:
            pass
        raise

    return tmp2


# ── 前缀命中率计算 ──

def compute_prefix_match(file_a: str, file_b: str) -> Tuple[int, int, float]:
    """
    计算 file_a 与 file_b 的前缀匹配情况。

    逐字符比较，直到遇到第一个不同字符或任一文件结束。

    Args:
        file_a: 上一轮的前缀文件（1.tmp）
        file_b: 当前轮的前缀文件（2.tmp）

    Returns:
        (match_chars, total_chars, match_rate)
        - match_chars: 匹配的字符数
        - total_chars: file_b 的总字符数
        - match_rate: 0.0 ~ 1.0
    """
    if not os.path.exists(file_a) or not os.path.exists(file_b):
        return 0, 0, 0.0

    content_a = ""
    content_b = ""

    try:
        with open(file_a, "r", encoding="utf-8") as f:
            content_a = f.read()
        with open(file_b, "r", encoding="utf-8") as f:
            content_b = f.read()
    except Exception:
        return 0, 0, 0.0

    total_b = len(content_b)
    if total_b == 0:
        return 0, 0, 0.0

    # 逐字符比较前缀
    match = 0
    min_len = min(len(content_a), total_b)
    for i in range(min_len):
        if content_a[i] == content_b[i]:
            match += 1
        else:
            break

    rate = match / total_b if total_b > 0 else 0.0
    return match, total_b, rate


# ── 命中率记录 ──

def record_hit_rate(home_dir: str, match_chars: int, total_chars: int, rate: float,
                    session_id: str = "") -> str:
    """
    将一轮的命中率追加到会话级 hit_rate/<session_id>.jsonl。

    同时写入人类可读日志 hit_rate/<session_id>.log。

    格式（JSONL 每行一条）:
      {"time":"...","match_chars":8500,"total_chars":10000,"rate":0.85}

    Returns:
        JSONL 文件路径
    """
    hit_path = _hit_rate_path(home_dir, session_id)
    log_path = _hit_rate_log_path(home_dir, session_id)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    entry = {
        "time": ts,
        "match_chars": match_chars,
        "total_chars": total_chars,
        "rate": round(rate, 4),
    }
    # JSONL（机器可读）
    with open(hit_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # 人类可读日志
    pct = f"{rate:.1%}"
    bar = _make_bar(rate, 20)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] match={match_chars}/{total_chars} {pct:>6} {bar}\n")

    return hit_path


def _make_bar(rate: float, width: int = 20) -> str:
    """生成命中率条形图"""
    filled = int(rate * width)
    if filled > width:
        filled = width
    return "█" * filled + "░" * (width - filled)


# ── 轮转 ──

def rotate_tmp_files(home_dir: str, session_id: str = "") -> None:
    """
    轮转 tmp 文件：清空 1.tmp → 将 2.tmp 的内容复制到 1.tmp。

    这使得下一轮调用时 1.tmp 是"上一轮的 2.tmp"，
    compute_prefix_match 可以对比前缀变化。

    Args:
        session_id: 当前会话 UUID，用于按会话隔离 tmp 文件
    """
    tmp1 = _tmp1_path(home_dir, session_id)
    tmp2 = _tmp2_path(home_dir, session_id)

    if not os.path.exists(tmp2):
        return

    with open(tmp2, "r", encoding="utf-8") as f:
        content = f.read()

    # 清空 1.tmp 并写入 2.tmp 的内容
    with open(tmp1, "w", encoding="utf-8") as f:
        f.write(content)


# ── 完整的追踪 + 轮转（一步完成）──

def track_and_rotate(home_dir: str, session_id: str = "") -> float:
    """
    记录命中率 + 轮转，一次调用完成。

    1. 如果 1.tmp 非空，计算与 2.tmp 的前缀匹配率 → 记录到 hit_rate/<session_id>.jsonl
    2. 将 2.tmp 复制到 1.tmp（为下一轮做准备）

    Args:
        session_id: 当前会话 UUID，用于按会话分文件记录

    Returns:
        本轮命中率 (0.0 ~ 1.0)，首轮返回 0.0
    """
    tmp1 = _tmp1_path(home_dir, session_id)
    tmp2 = _tmp2_path(home_dir, session_id)

    # 检查 1.tmp 是否为空（首轮跳过）
    if os.path.exists(tmp1) and os.path.getsize(tmp1) > 0:
        match, total, rate = compute_prefix_match(tmp1, tmp2)
        record_hit_rate(home_dir, match, total, rate, session_id)
    else:
        rate = 0.0
        # 首轮也记录（match=0 表示无可比较的前一轮）
        if os.path.exists(tmp2):
            total = os.path.getsize(tmp2)
            total = max(total, 1)
            record_hit_rate(home_dir, 0, total, 0.0, session_id)

    # 轮转
    rotate_tmp_files(home_dir, session_id)

    return rate


# ── 读取前缀（供 API 注入）──

def get_prompt_prefix(home_dir: str, session_id: str = "") -> str:
    """
    读取 2.tmp 的内容，作为 API 请求的 stable prefix。
    返回空字符串如果 2.tmp 不存在。

    Args:
        session_id: 当前会话 UUID，用于按会话隔离 tmp 文件
    """
    tmp2 = _tmp2_path(home_dir, session_id)
    if not os.path.exists(tmp2):
        return ""
    try:
        with open(tmp2, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


# ── 汇总 ──

def summarize_hit_rates(home_dir: str, session_id: str = "") -> dict:
    """
    从 hit_rate/<session_id>.jsonl 读取命中率记录，计算汇总。

    Returns:
        {
            "count": N,              # 总轮数
            "rates": [0.85, ...],    # 全部命中率（用于详细日志）
            "last_5": [0.88, ...],   # 最近 5 轮（用于摘要显示）
            "avg_rate": 0.82,        # 平均命中率
            "last_rate": 0.90,       # 最后一次命中率
            "total_match": 85000,    # 总命中字符数
            "total_chars": 100000,   # 总字符数
            "overall_rate": 0.85,    # 总命中率
            "log_path": "/.../xxx.log",  # 人类可读详细日志路径
        }
    """
    hit_path = _hit_rate_path(home_dir, session_id)
    log_path = _hit_rate_log_path(home_dir, session_id)

    empty = {
        "count": 0,
        "rates": [],
        "last_5": [],
        "avg_rate": 0.0,
        "last_rate": 0.0,
        "total_match": 0,
        "total_chars": 0,
        "overall_rate": 0.0,
        "log_path": log_path,
    }

    if not os.path.exists(hit_path):
        return empty

    rates = []
    total_match = 0
    total_chars = 0

    try:
        with open(hit_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    r = entry.get("rate", 0.0)
                    rates.append(r)
                    total_match += entry.get("match_chars", 0)
                    total_chars += entry.get("total_chars", 0)
                except json.JSONDecodeError:
                    continue
    except Exception:
        return empty

    count = len(rates)
    avg_rate = sum(rates) / count if count > 0 else 0.0
    last_rate = rates[-1] if rates else 0.0
    overall_rate = total_match / total_chars if total_chars > 0 else 0.0
    # 最近 5 轮
    last_5 = rates[-5:] if count >= 5 else rates[:]

    return {
        "count": count,
        "rates": rates,
        "last_5": last_5,
        "avg_rate": avg_rate,
        "last_rate": last_rate,
        "total_match": total_match,
        "total_chars": total_chars,
        "overall_rate": overall_rate,
        "log_path": log_path,
    }


def format_hit_rate_summary(home_dir: str, lang: str = "chinese",
                            session_id: str = "") -> str:
    """
    格式化命中率汇总（仅显示最近 5 轮明细，完整日志在 .log 文件）。
    """
    s = summarize_hit_rates(home_dir, session_id)

    if s["count"] == 0:
        return (
            "📊 前缀缓存命中率: 无记录（仅一轮或未初始化）"
            if lang == "chinese"
            else "📊 Prefix cache hit rate: no records (single round or uninitialized)"
        )

    # 最近 5 轮明细
    last_5_str = " → ".join(f"{r:.1%}" for r in s["last_5"])

    if lang == "chinese":
        lines = [
            "📊 前缀缓存命中率汇总",
            f"   比较轮次: {s['count']} 轮  |  平均: {s['avg_rate']:.1%}  |  最终: {s['last_rate']:.1%}",
            f"   总命中: {s['total_match']:,} / {s['total_chars']:,} ({s['overall_rate']:.1%})",
            f"   最近 {len(s['last_5'])} 轮: {last_5_str}",
            f"   💾 完整日志: {s['log_path']}",
        ]
    else:
        lines = [
            "📊 Prefix Cache Hit Rate Summary",
            f"   Rounds: {s['count']}  |  Avg: {s['avg_rate']:.1%}  |  Last: {s['last_rate']:.1%}",
            f"   Total: {s['total_match']:,} / {s['total_chars']:,} ({s['overall_rate']:.1%})",
            f"   Last {len(s['last_5'])}: {last_5_str}",
            f"   💾 Full log: {s['log_path']}",
        ]

    return "\n".join(lines)


def get_hit_rate_log_path(home_dir: str, session_id: str = "") -> str:
    """返回命中率详细日志的路径（供 Ctrl+X 查阅）。"""
    return _hit_rate_log_path(home_dir, session_id)


# ── 前缀剥离（library 归档用）──

def strip_prompt_prefix(content: str) -> str:
    """
    从内容中剥离前缀（分隔线以上部分）。

    在分隔线 "--- PROMPT SEPARATOR ---" 处截断，
    返回分隔线之后的内容。如果找不到分隔线，返回原内容。

    用于 library 归档时去除缓存前缀。
    """
    separator = "--- PROMPT SEPARATOR ---"
    idx = content.find(separator)
    if idx == -1:
        return content

    # 跳到分隔线之后（跳过行尾换行）
    after = idx + len(separator)
    # 跳过紧随的换行符
    while after < len(content) and content[after] in ("\n", "\r"):
        after += 1

    return content[after:]
