# -*- coding: utf-8 -*-
"""
timeline.py — Onyx 记忆时间线（time/ 树 + timeline.json 分层摘要）

数据布局（记忆根 ~/.ai_s/ 下）：
  time/
    YYYY/
      MM/
        YYYY-M-D/
          list.json   — 当日任务列表（与旧 chat.json messages 同构）
  timeline.json       — {"days": {"2026-8-12": "..."},
                         "months": {"2026-7": "..."},
                         "years": {"2026": "..."},
                         "last_recorded": "2026-8-12"}

功能：
  record_session()             — 把一次交互写入当日 list.json（由 storage.record_ai_session 调用）
  list_timeline()              — day / month / year / 日期区间 三级查询
  ensure_boundary_summaries()  — 跨日/月/年边界时用当前系列最便宜模型生成摘要写入 timeline.json
  migrate_from_chat()          — 一次性迁移：旧 chat.json messages 按 timestamp 拆到 time/

设计原则：全部 try/except 兜底，任何失败静默降级，不影响主流程。
"""
import os
import json
import time as _time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .i18n import _ as _i18n  # 双语文本（中英）


# ── 路径与日期工具 ──

def _time_root(home_dir: str) -> str:
    """记忆根下的 time/ 目录。"""
    return os.path.join(home_dir, ".ai_s", "time")


def _timeline_json_path(home_dir: str) -> str:
    return os.path.join(home_dir, ".ai_s", "timeline.json")


def _parse_date(s: str) -> Optional[datetime]:
    """解析 'YYYY-M-D'（如 2026-2-12）→ datetime；非法返回 None（防路径穿越）。"""
    if not s or not isinstance(s, str):
        return None
    parts = s.strip().split("-")
    if len(parts) != 3:
        return None
    try:
        y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
        if not (1 <= y <= 9999 and 1 <= m <= 12 and 1 <= d <= 31):
            return None
        return datetime(y, m, d)
    except (ValueError, TypeError):
        return None


def _fmt_date(dt: datetime) -> str:
    """datetime → 'YYYY-M-D'（统一无前导零）。"""
    return f"{dt.year}-{dt.month}-{dt.day}"


def _fmt_month(dt: datetime) -> str:
    return f"{dt.year}-{dt.month}"


def _day_dir(home_dir: str, dt: datetime) -> str:
    """日期 → time/YYYY/MM/YYYY-M-D/ 目录（不创建）。"""
    return os.path.join(_time_root(home_dir), str(dt.year), f"{dt.month:02d}", _fmt_date(dt))


def _day_list_path(home_dir: str, dt: datetime) -> str:
    return os.path.join(_day_dir(home_dir, dt), "list.json")


# ── list.json 读写（与 chat.json 同构：{date, messages:[...]}）──

def load_day_list(home_dir: str, dt: datetime) -> Dict[str, Any]:
    """读取指定日期的 list.json；不存在返回空结构。"""
    path = _day_list_path(home_dir, dt)
    if not os.path.exists(path):
        return {"date": _fmt_date(dt), "messages": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("messages"), list):
            return data
    except Exception:
        pass
    return {"date": _fmt_date(dt), "messages": []}


def save_day_list(home_dir: str, dt: datetime, data: Dict[str, Any]) -> None:
    """原子写入 list.json（tmp + rename + fsync，与 chat.json 一致）。"""
    d = _day_dir(home_dir, dt)
    os.makedirs(d, exist_ok=True)
    path = _day_list_path(home_dir, dt)
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        raise


def record_session(home_dir: str, session_id: str, user_question: str,
                   ai_result: Dict[str, Any] = None, tag: str = "",
                   class_level: str = "1", timestamp: str = "") -> Optional[str]:
    """把一次交互写入当日 list.json，返回 message_id（失败返回 None）。

    由 storage.record_ai_session 调用；list.json 与旧 chat.json messages 同构，
    功能完全一致（当日任务的 session uuid 索引）。
    """
    try:
        if not session_id:
            return None
        ai_result = ai_result or {}
        if not timestamp:
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        dt = datetime.now()
        msg = {
            "id": __import__("secrets").token_hex(4),
            "session_uuid": session_id,
            "timestamp": timestamp,
            "user_question": (user_question or "")[:5000],
            "ai_response": (ai_result.get("txt", "") or "")[:5000],
            "tag": tag or ai_result.get("tag", "") or "",
            "class": class_level or ai_result.get("class", "1") or "1",
        }
        data = load_day_list(home_dir, dt)
        # 去重：同一 session 已记录过则跳过（record_ai_session 可能多次调用）
        for m in data.get("messages", []):
            if m.get("session_uuid") == session_id:
                return m.get("id")
        data.setdefault("messages", []).append(msg)
        save_day_list(home_dir, dt, data)
        return msg["id"]
    except Exception:
        return None


# ── timeline.json 读写 ──

def load_timeline(home_dir: str) -> Dict[str, Any]:
    path = _timeline_json_path(home_dir)
    if not os.path.exists(path):
        return {"days": {}, "months": {}, "years": {}, "last_recorded": ""}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for k in ("days", "months", "years"):
                if not isinstance(data.get(k), dict):
                    data[k] = {}
            if not isinstance(data.get("last_recorded"), str):
                data["last_recorded"] = ""
            return data
    except Exception:
        pass
    return {"days": {}, "months": {}, "years": {}, "last_recorded": ""}


def save_timeline(home_dir: str, data: Dict[str, Any]) -> None:
    path = _timeline_json_path(home_dir)
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        raise


# ── 最便宜模型摘要调用（当前系列最低价 LLM，非流式）──

def _call_cheap_llm(messages: List[Dict], timeout: float = 25.0) -> str:
    """用当前平台最便宜模型做一次非流式补全；失败返回空串。"""
    try:
        import requests
        from .config import load_key_conf, _SUPPORTED_PLATFORMS
        from .cost import resolve_cheapest_model

        conf = load_key_conf() or {}
        if not conf.get("api_key"):
            return ""
        plat = conf.get("platform", "deepseek")
        info = _SUPPORTED_PLATFORMS.get(plat) or _SUPPORTED_PLATFORMS.get("deepseek", {})
        if plat == "custom":
            url = conf.get("api_url", "https://api.openai.com/v1/chat/completions")
            model = conf.get("model", "gpt-4")
        else:
            url = info.get("api_url", "")
            model = resolve_cheapest_model(plat) or info.get("default_model", "")
        if not url or not model:
            return ""
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 400,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {conf['api_key']}",
            "Content-Type": "application/json",
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        return (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
    except Exception:
        return ""


def _summarize_day(home_dir: str, dt: datetime) -> str:
    """生成某日 100 字摘要（最便宜模型）；失败返回空串。"""
    data = load_day_list(home_dir, dt)
    msgs = data.get("messages", [])
    if not msgs:
        return ""
    lines = []
    for m in msgs[:20]:
        q = (m.get("user_question", "") or "").strip().replace("\n", " ")
        if q:
            lines.append(f"- {q[:120]}")
    if not lines:
        return ""
    prompt = (
        f"请用100字以内（中文）总结 {_fmt_date(dt)} 这一天完成的工作内容。\n"
        f"这一天共 {len(msgs)} 个任务：\n" + "\n".join(lines)
    )
    return _call_cheap_llm([
        {"role": "system", "content": "你是记忆整理助手，输出简洁、客观、只包含事实的日总结。"},
        {"role": "user", "content": prompt},
    ])


def _summarize_month(home_dir: str, y: int, m: int) -> str:
    """生成某月整体描述（基于该月已有日摘要）。"""
    tl = load_timeline(home_dir)
    days = tl.get("days", {})
    entries = []
    for d_key, summary in sorted(days.items()):
        d = _parse_date(d_key)
        if d and d.year == y and d.month == m:
            entries.append(f"- {d_key}: {summary[:100]}")
    if not entries:
        return ""
    prompt = (
        f"请用150字以内（中文）总结 {y}年{m}月 的整体工作情况（基于每日摘要）。\n"
        + "\n".join(entries[:31])
    )
    return _call_cheap_llm([
        {"role": "system", "content": "你是记忆整理助手，输出简洁、客观、只包含事实的月度总结。"},
        {"role": "user", "content": prompt},
    ])


def _summarize_year(home_dir: str, y: int) -> str:
    """生成某年整体描述（基于该年已有月摘要）。"""
    tl = load_timeline(home_dir)
    months = tl.get("months", {})
    entries = []
    for mk, summary in sorted(months.items()):
        parts = mk.split("-")
        if len(parts) == 2 and parts[0] == str(y):
            entries.append(f"- {mk}: {summary[:120]}")
    if not entries:
        return ""
    prompt = (
        f"请用200字以内（中文）总结 {y}年 的整体工作情况（基于每月摘要）。\n"
        + "\n".join(entries[:12])
    )
    return _call_cheap_llm([
        {"role": "system", "content": "你是记忆整理助手，输出简洁、客观、只包含事实的年度总结。"},
        {"role": "user", "content": prompt},
    ])


# ── 边界惰性摘要 ──

def ensure_boundary_summaries(home_dir: str) -> bool:
    """跨边界惰性摘要：比较 last_recorded 与今天，补齐缺失的日/月/年摘要。

    - 跨日：对 last_recorded 之后到昨天的每一天，生成 100 字日摘要
    - 跨月：last_recorded 所在月的上月，生成月描述
    - 跨年：last_recorded 所在年的上年，生成年描述
    全部写入 timeline.json；任何失败静默降级。返回是否有新摘要生成。
    """
    try:
        tl = load_timeline(home_dir)
        last = tl.get("last_recorded", "") or ""
        today = datetime.now().date()
        last_dt = _parse_date(last) if last else None

        # ── 日摘要：从 last 到昨天（最多补最近 14 天，避免一次性轰炸）──
        # 语义：跨日后的第一次询问，生成「前一天」（即 last 记录日）的 100 字摘要。
        changed = False
        days = tl.setdefault("days", {})
        if last_dt:
            cursor = last_dt.date()
            end = today - timedelta(days=1)
            count = 0
            while cursor <= end and count < 14:
                dk = _fmt_date(cursor)
                if dk not in days:
                    summary = _summarize_day(home_dir, datetime(cursor.year, cursor.month, cursor.day))
                    if summary:
                        days[dk] = summary[:200]
                        changed = True
                cursor += timedelta(days=1)
                count += 1

        # ── 月摘要：last 所在月起，到上个月（不含本月，本月未结束不生成）──
        months = tl.setdefault("months", {})
        if last_dt:
            last_month = last_dt.date().replace(day=1)
            cur_month = today.replace(day=1)
            cursor = last_month
            while cursor < cur_month:
                mk = f"{cursor.year}-{cursor.month}"
                if mk not in months:
                    summary = _summarize_month(home_dir, cursor.year, cursor.month)
                    if summary:
                        months[mk] = summary[:400]
                        changed = True
                cursor = _add_months(cursor, 1)

        # ── 年摘要：last 年起，到去年（不含今年）──
        years = tl.setdefault("years", {})
        if last_dt:
            for y in range(last_dt.year, today.year):
                yk = str(y)
                if yk not in years:
                    summary = _summarize_year(home_dir, y)
                    if summary:
                        years[yk] = summary[:600]
                        changed = True

        # 更新 last_recorded（仅当有过往记录且今天晚于 last；避免空库每轮触发）
        if last_dt and today > last_dt.date():
            tl["last_recorded"] = _fmt_date(today)
            save_timeline(home_dir, tl)
            return changed
        return changed
    except Exception:
        return False


def _add_months(dt, months: int):
    """月份加减（跨年安全，日期钳制到月末）。"""
    m = dt.month - 1 + months
    y = dt.year + m // 12
    m = m % 12 + 1
    import calendar
    d = min(dt.day, calendar.monthrange(y, m)[1])
    return dt.replace(year=y, month=m, day=d)


# ── 查询：list_timeline ──

def _render_day_list(home_dir: str, dt: datetime) -> str:
    """渲染当日任务列表（与旧 load_chat_memory_for_context 同构）。"""
    data = load_day_list(home_dir, dt)
    msgs = data.get("messages", [])
    if not msgs:
        return _i18n("timeline_day_empty", "bilingual", date=_fmt_date(dt)) if hasattr(_i18n, "__call__") else f"（{_fmt_date(dt)} 无任务记录）"
    lines = [f"# {_fmt_date(dt)} 任务列表（{len(msgs)}）"]
    for i, m in enumerate(reversed(msgs[-30:]), 1):
        sid = m.get("session_uuid", "?")
        q = (m.get("user_question", "") or "")[:120].replace("\n", " ")
        tag = f" [{m.get('tag', '')}]" if m.get("tag") else ""
        lines.append(f"{i}. [{m.get('id', '?')}](library/{sid}){tag} — {q}")
    return "\n".join(lines)


def list_timeline(home_dir: str, day: str = "", month: str = "",
                  year: str = "", start: str = "", end: str = "") -> str:
    """三级时间表查询。

    day   = '2026-2-12'        → 当日任务列表（list.json）
    month = '2026-6'           → 该月每日描述（timeline.json days 过滤）
    year  = '2026'             → 该年每月描述（timeline.json months 过滤）
    start/end = '2026-6-7','2026-6-8' → 区间内逐日任务列表
    """
    try:
        tl = load_timeline(home_dir)
        days = tl.get("days", {})
        months = tl.get("months", {})
        years = tl.get("years", {})

        # ── day：当日任务列表 ──
        if day:
            dt = _parse_date(day)
            if not dt:
                return _i18n("timeline_bad_date", "bilingual", value=day)
            summary = days.get(_fmt_date(dt), "")
            body = _render_day_list(home_dir, dt)
            if summary:
                body = f"📝 日摘要：{summary}\n\n{body}"
            return body

        # ── month：该月每日描述 ──
        if month:
            parts = month.split("-")
            if len(parts) != 2:
                return _i18n("timeline_bad_month", "bilingual", value=month)
            try:
                y, m = int(parts[0]), int(parts[1])
                if not (1 <= m <= 12):
                    return _i18n("timeline_bad_month", "bilingual", value=month)
            except ValueError:
                return _i18n("timeline_bad_month", "bilingual", value=month)
            m_key = f"{y}-{m}"
            month_summary = months.get(m_key, "")
            lines = [f"# {m_key} 每日描述"]
            for dk, s in sorted(days.items()):
                d = _parse_date(dk)
                if d and d.year == y and d.month == m:
                    lines.append(f"- {dk}: {s}")
            if len(lines) == 1:
                lines.append(_i18n("timeline_month_empty", "bilingual"))
            body = "\n".join(lines)
            if month_summary:
                body = f"📝 月摘要：{month_summary}\n\n{body}"
            return body

        # ── year：该年每月描述 ──
        if year:
            try:
                y = int(year)
            except ValueError:
                return _i18n("timeline_bad_year", "bilingual", value=year)
            y_key = str(y)
            year_summary = years.get(y_key, "")
            lines = [f"# {y_key} 每月描述"]
            for mk, s in sorted(months.items()):
                if mk.startswith(f"{y}-"):
                    lines.append(f"- {mk}: {s}")
            if len(lines) == 1:
                lines.append(_i18n("timeline_year_empty", "bilingual"))
            body = "\n".join(lines)
            if year_summary:
                body = f"📝 年摘要：{year_summary}\n\n{body}"
            return body

        # ── 区间：start ~ end 逐日 ──
        if start or end:
            d0 = _parse_date(start) if start else None
            d1 = _parse_date(end) if end else None
            if not d0:
                return _i18n("timeline_bad_date", "bilingual", value=start)
            if not d1:
                return _i18n("timeline_bad_date", "bilingual", value=end)
            if d1 < d0:
                d0, d1 = d1, d0
            if (d1.date() - d0.date()).days > 90:
                return _i18n("timeline_range_too_big", "bilingual")
            parts = []
            cursor = d0.date()
            while cursor <= d1.date():
                parts.append(_render_day_list(home_dir, datetime(cursor.year, cursor.month, cursor.day)))
                cursor += timedelta(days=1)
            return "\n\n".join(parts)

        # ── 无参数：总览（last_recorded + 各层条目数）──
        lines = [
            f"# 时间线总览",
            f"- 最后记录日: {tl.get('last_recorded', '无')}",
            f"- 日摘要: {len(days)} 条",
            f"- 月描述: {len(months)} 条",
            f"- 年描述: {len(years)} 条",
        ]
        for mk, s in sorted(months.items())[-6:]:
            lines.append(f"- {mk}: {s[:80]}")
        return "\n".join(lines)
    except Exception as e:
        return _i18n("timeline_error", "bilingual", err=e)


# ── 一次性迁移 ──

def migrate_from_chat(home_dir: str, chat_name: str = None) -> Dict[str, int]:
    """把旧 chat.json messages 按 timestamp 日期拆到 time/YYYY/MM/YYYY-M-D/list.json。

    返回 {"migrated": n, "days": m}；幂等（按 id 去重）。
    若 timeline.json 尚无 last_recorded，顺带初始化为最晚任务日期。
    """
    from .storage import get_current_chat_name, load_chat_json
    if chat_name is None:
        chat_name = get_current_chat_name(home_dir)
    data = load_chat_json(home_dir, chat_name)
    msgs = data.get("messages", [])
    by_day: Dict[str, List[Dict]] = {}
    for m in msgs:
        ts = m.get("timestamp", "")
        try:
            dt = datetime.strptime(ts, '%Y-%m-%d %H:%M:%S')
        except (ValueError, TypeError):
            continue
        dk = _fmt_date(dt)
        by_day.setdefault(dk, []).append(m)

    migrated = 0
    for dk, items in by_day.items():
        dt = _parse_date(dk)
        if not dt:
            continue
        cur = load_day_list(home_dir, dt)
        existing_ids = {m.get("id") for m in cur.get("messages", [])}
        added = 0
        for m in items:
            if m.get("id") not in existing_ids:
                cur.setdefault("messages", []).append(m)
                existing_ids.add(m.get("id"))
                added += 1
        if added:
            cur["date"] = dk
            save_day_list(home_dir, dt, cur)
            migrated += added

    # 初始化 last_recorded（仅当为空时）：取最晚任务日期
    try:
        tl = load_timeline(home_dir)
        if not tl.get("last_recorded"):
            latest = max((_parse_date(dk) for dk in by_day if _parse_date(dk)), default=None)
            if latest:
                tl["last_recorded"] = _fmt_date(latest)
                save_timeline(home_dir, tl)
    except Exception:
        pass

    return {"migrated": migrated, "days": len(by_day)}
