# -*- coding: utf-8 -*-
"""AI 模式开关 — REPL / TUI 双模式的唯一控制点。

低耦合设计（本文件是两模式唯一的耦合点）：
  - 默认模式只由下方 DEFAULT_AI_MODE 一个变量决定；
  - 裸 `ai` 走默认模式，`ai -repl` / `ai -tui` 显式覆盖；
  - 两个模式的实现分别位于 bin/ai_interactive.py（REPL）与 bin/ai_tui.py（TUI），
    彼此不 import、不感知对方。

切换默认模式，只改这一行：
    DEFAULT_AI_MODE = "repl"      # 或 "tui"

覆盖优先级：显式 flag > 环境变量 ONYX_AI_MODE > etc/config.json(ai.default_mode) > DEFAULT_AI_MODE
"""
import os

# ============================================================
# ★ 唯一开关：默认 AI 模式（"repl" | "tui"）
# ============================================================
DEFAULT_AI_MODE = "tui"

# 当前进程渲染模式（由会话入口设置；"repl" | "tui"）
RENDER_MODE = "repl"

_VALID = ("repl", "tui")

# 命令行模式标志（紧跟 `ai` 的第 2 个 token 才识别，避免误伤 prompt 文本）
_MODE_FLAGS = {
    "-repl": "repl", "--repl": "repl", "repl": "repl",
    "-tui": "tui", "--tui": "tui", "tui": "tui",
}


def _repo_root() -> str:
    """由本文件位置推导仓库根（bin/ai_lib/mode.py → 上溯三级）。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _config_default():
    """可选：读取 etc/config.json 的 ai.default_mode（不存在/非法则 None）。"""
    try:
        import json
        p = os.path.join(_repo_root(), "etc", "config.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            v = (data.get("ai") or {}).get("default_mode")
            if v in _VALID:
                return v
    except Exception:
        pass
    return None


def resolve_ai_mode(explicit=None) -> str:
    """解析最终 AI 模式。

    优先级：explicit（-repl/-tui）> 环境变量 ONYX_AI_MODE > etc/config.json > DEFAULT_AI_MODE
    """
    if explicit in _VALID:
        return explicit
    env = os.environ.get("ONYX_AI_MODE")
    if env in _VALID:
        return env
    cfg = _config_default()
    if cfg in _VALID:
        return cfg
    return DEFAULT_AI_MODE if DEFAULT_AI_MODE in _VALID else "repl"


def split_mode_flag(cmd_parts):
    """从 ai 命令参数中提取模式标志。

    仅识别紧跟 `ai` 的第 2 个 token（`ai -tui ...`），避免把 prompt 文本里的
    "repl"/"tui" 误判为模式。

    返回: (mode | None, 去掉模式标志后的 cmd_parts)
    """
    if not cmd_parts:
        return None, cmd_parts
    rest = list(cmd_parts)
    mode = None
    if len(rest) >= 2 and isinstance(rest[1], str) and rest[1].lower() in _MODE_FLAGS:
        mode = _MODE_FLAGS[rest[1].lower()]
        rest = [rest[0]] + rest[2:]
    return mode, rest


def set_render_mode(mode: str) -> None:
    """设置当前进程渲染模式（会话入口调用）。"""
    global RENDER_MODE
    RENDER_MODE = mode if mode in _VALID else "repl"


def is_tui_render() -> bool:
    return RENDER_MODE == "tui"
