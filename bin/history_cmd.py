# -*- coding: utf-8 -*-
"""
history 命令 — 查看用户向 AI 提问的历史记录。

数据源：~/.ai_s/chat/<chat>.json 的 messages[]。
每次 AI 交互（handle_ai 成功产出回复/追问）都会经 append_message_to_chat
记录 user_question / timestamp / tag / class，本命令只读展示。

用法：
  history            查看当前 chat 最近 10 条提问
  history 20         查看最近 20 条提问
  history -c <chat>  查看指定 chat 的提问（-c / --chat）
  history -a         查看所有 chat 的提问（-a / --all，跨 chat 按时间倒序取最新）
  history --help     帮助
"""

import os
from typing import Dict, List, Optional


def get_lang_msgs(current_lang: str) -> Dict[str, Dict[str, str]]:
    return {
        "history": {
            "chinese": {
                "usage": "用法：history [条数] | history -c <chat> | history -a | history --help",
                "help": "功能：查看你向 AI 问过的问题（记录在 ~/.ai_s/chat/<chat>.json）",
                "empty": "暂无提问记录。",
                "no_chat": "没有找到 chat '{}'。可用 history -a 查看全部，或 ai -c list 列出所有 chat。",
                "header": "AI 提问历史",
                "count_hint": "（显示最近 {} 条，共 {} 条）",
            },
            "english": {
                "usage": "Usage: history [count] | history -c <chat> | history -a | history --help",
                "help": "Function: view the questions you asked the AI (stored in ~/.ai_s/chat/<chat>.json)",
                "empty": "No question history yet.",
                "no_chat": "Chat '{}' not found. Use history -a to see all, or ai -c list to list chats.",
                "header": "AI question history",
                "count_hint": "（showing last {} of {} entries）",
            },
        }
    }


def _format_question(question: str, max_chars: int = 120) -> str:
    """单行化 + 截断，避免终端换行刷屏。"""
    one_line = " ".join(question.split())
    if len(one_line) > max_chars:
        return one_line[:max_chars] + "…"
    return one_line


def handle_history(cmd_parts: List[str], request_id: str,
                   _home_dir: Optional[str] = None) -> None:
    """history 主入口。_home_dir 仅供测试注入，缺省走 Onyx 的 USER_HOME_DIR。"""
    from Onyx import USER_HOME_DIR, global_config
    from lib.terminal.colors import Fore, Style

    home_dir = _home_dir or USER_HOME_DIR
    current_lang = (global_config.get("display_info", {})
                    .get("language", {}).get("current", "chinese"))
    lang_msgs = get_lang_msgs(current_lang)["history"][current_lang]

    # ── 解析参数 ──
    limit = 10
    chat_filter: Optional[str] = None
    all_chats = False
    args = cmd_parts[1:]
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-c", "--chat"):
            if i + 1 < len(args):
                chat_filter = args[i + 1]
                i += 2
                continue
            print(Fore.RED + f"history: '{a}' 需要一个 chat 名称" + Style.RESET_ALL)
            return
        elif a in ("-a", "--all"):
            all_chats = True
        elif a in ("-h", "--help"):
            print(Fore.YELLOW + lang_msgs["usage"] + "\n" + lang_msgs["help"] + Style.RESET_ALL)
            return
        elif a.isdigit():
            limit = max(1, int(a))
        i += 1

    # ── 收集记录 ──
    from bin.ai_lib.storage import get_current_chat_name, load_chat_json, list_chat_memories

    chats = list_chat_memories(home_dir)
    if chat_filter is not None:
        if chat_filter not in chats:
            print(Fore.RED + lang_msgs["no_chat"].format(chat_filter) + Style.RESET_ALL)
            return
        chats = [chat_filter]
    elif not all_chats:
        cur = get_current_chat_name(home_dir)
        chats = [cur] if cur in chats else chats

    rows: List[Dict] = []
    for c in chats:
        data = load_chat_json(home_dir, c)
        for m in data.get("messages", []):
            q = (m.get("user_question") or "").strip()
            if not q:
                continue
            rows.append({
                "chat": c,
                "time": m.get("timestamp", ""),
                "question": q,
                "tag": m.get("tag", ""),
            })

    if not rows:
        print(Fore.YELLOW + lang_msgs["empty"] + Style.RESET_ALL)
        return

    # 按时间倒序取最新 N 条，再正序展示（编号从旧到新）
    rows.sort(key=lambda r: r["time"], reverse=True)
    shown = rows[:limit]
    shown.reverse()

    header = lang_msgs["header"]
    if all_chats:
        header += "（all chats）"
    elif chat_filter is not None:
        header += f"（chat: {chat_filter}）"
    else:
        header += f"（chat: {get_current_chat_name(home_dir)}）"
    print(Fore.CYAN + f"── {header} {lang_msgs['count_hint'].format(len(shown), len(rows))} ──" + Style.RESET_ALL)

    for idx, r in enumerate(shown, 1):
        time_str = r["time"]
        tag_str = f" [#{r['tag']}]" if r.get("tag") else ""
        chat_str = f"[{r['chat']}] " if (all_chats or chat_filter is not None) else ""
        print(f"  {idx:>3}  {time_str}  {chat_str}{_format_question(r['question'])}{tag_str}")
