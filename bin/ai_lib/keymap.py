# -*- coding: utf-8 -*-
"""AI 按键绑定注册表 —— 所有属于 AI 的操作键都可由用户自选，默认保持原有键位。

设计：
  · ACTIONS 是唯一事实来源：动作 id / 分组 / 双语说明 / 默认键列表；
  · 用户覆盖存到 `<user_home>/.config/onyx/ai/keymap.json`（缺失即用默认）；
  · 键名用 **Textual 风格**（`ctrl+r` / `alt+enter` / `pageup`；多键序列用逗号
    连接，如 `escape,enter`），REPL(prompt_toolkit) 侧由 `to_ptk()` 转换；
  · **多行/换行键的默认值随平台变化**：标准 Linux/Windows/macOS 桌面终端默认
    `shift+enter`；手机 Termux（软键盘没有 Shift+Enter）默认沿用原来的 `alt+enter`。
    两种键互为别名，都能用。
  · 非法组合 / 重复占用一律拒绝（`set_binding` 返回 False），配置界面据此提示。

用法：
    from bin.ai_lib import keymap
    keymap.init(user_home_dir)
    keymap.combos("tui.history_search")        # → ['ctrl+r']
    keymap.set_binding("tui.history_search", ["f2"])
    keymap.reset("tui.history_search")
"""
import json
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

# 分组顺序（配置界面按此分组展示）
GROUP_ORDER = ("tui", "multiline", "complete", "repl")

GROUP_LABELS = {
    "tui": ("AI TUI 主界面", "AI TUI main"),
    "multiline": ("AI TUI 多行输入框", "AI TUI multiline box"),
    "complete": ("AI TUI 补全菜单", "AI TUI completion menu"),
    "repl": ("AI 对话模式（非 TUI）", "AI chat mode (non-TUI)"),
}

# (动作 id, 分组, 中文说明, English, 默认键列表)
# 默认键 = 本仓库改动前的原有键位，行为完全不变。
ACTIONS: List[Tuple[str, str, str, str, List[str]]] = [
    # ── TUI 主界面 ──
    ("tui.cancel", "tui", "取消当前生成", "Cancel generation", ["ctrl+c"]),
    ("tui.quit", "tui", "退出", "Quit", ["ctrl+q"]),
    ("tui.eof_quit", "tui", "空输入时退出", "Quit on empty input", ["ctrl+d"]),
    ("tui.history_prev", "tui", "上一条历史", "Previous history", ["ctrl+p"]),
    ("tui.history_next", "tui", "下一条历史", "Next history", ["ctrl+n"]),
    ("tui.history_search", "tui", "历史搜索", "History search", ["ctrl+r"]),
    ("tui.clear_log", "tui", "清屏", "Clear log", ["ctrl+l"]),
    ("tui.word_left", "tui", "光标左移一个词", "Word left", ["alt+b"]),
    ("tui.word_right", "tui", "光标右移一个词", "Word right", ["alt+f"]),
    ("tui.scroll_up", "tui", "日志上翻页", "Scroll log up", ["pageup"]),
    ("tui.scroll_down", "tui", "日志下翻页", "Scroll log down", ["pagedown"]),
    ("tui.multiline", "tui", "进入多行输入 / 换行", "Enter multiline / newline",
     None),   # None = 平台相关默认（见 multiline_default_keys）

    # ── TUI 多行输入框 ──
    ("multiline.send", "multiline", "多行框：发送", "Multiline: send",
     None),   # None = 平台相关默认（见 multiline_default_keys）

    # ── TUI 补全菜单 ──
    # complete.accept(Tab)：菜单已开时**选中并插入下一项**（循环），菜单关着则只打开菜单
    # —— 与主 REPL 的 completion_next / AI REPL 的 _complete_next 一致，不再是「只接受高亮项」。
    ("complete.accept", "complete", "循环补全下一项", "Cycle to next completion", ["tab"]),
    ("complete.next", "complete", "下一项补全", "Next completion", ["down"]),
    ("complete.prev", "complete", "上一项补全", "Previous completion", ["up"]),

    # ── AI 对话模式（prompt_toolkit）──
    ("repl.submit", "repl", "发送", "Submit", ["enter", "ctrl+j"]),
    ("repl.newline", "repl", "换行（多行）", "Newline (multiline)",
     ["escape,enter", "escape,ctrl+j"]),
    ("repl.complete", "repl", "触发补全 / 下一项", "Complete / next", ["ctrl+i"]),
    ("repl.complete_prev", "repl", "补全上一项", "Complete previous", ["shift+tab"]),
    ("repl.cancel", "repl", "取消当前输入", "Cancel input", ["ctrl+c"]),
    ("repl.quit", "repl", "空输入时退出", "Quit on empty input", ["ctrl+d"]),
]

# ── 多行/换行键的平台相关默认值 ──
# 手机 Termux 的软键盘没有 Shift+Enter → 沿用原来的 Alt+Enter；
# 桌面终端（Linux/Windows/macOS）→ 默认 Shift+Enter。
# 说明：TUI 字节层会把 ESC+CR / ESC+LF 改写成 CSI-u shift+enter，所以桌面按
#       Shift+Enter 或 Alt+Enter 都能命中；两种键同时保留为别名。
_TERMUX_CACHE = None


def is_termux() -> bool:
    """是否运行在 Android/Termux（手机端）。"""
    global _TERMUX_CACHE
    if _TERMUX_CACHE is None:
        try:
            if (os.path.exists("/data/data/com.termux/files/home")
                    and os.path.exists("/data/data/com.termux/files/usr")):
                _TERMUX_CACHE = True
            elif "termux" in getattr(sys, "prefix", "").lower():
                _TERMUX_CACHE = True
            elif "com.termux" in (os.environ.get("PREFIX") or ""):
                _TERMUX_CACHE = True
            else:
                _TERMUX_CACHE = False
        except Exception:
            _TERMUX_CACHE = False
    return _TERMUX_CACHE


_DESKTOP_ML_KEYS = ["shift+enter", "alt+enter", "alt+ctrl+j", "ctrl+alt+j"]
_TERMUX_ML_KEYS = ["alt+enter", "shift+enter", "alt+ctrl+j", "ctrl+alt+j"]


def multiline_default_keys() -> List[str]:
    """多行/换行键的默认值：桌面 Shift+Enter，Termux Alt+Enter。"""
    return list(_TERMUX_ML_KEYS if is_termux() else _DESKTOP_ML_KEYS)


def _resolve_defaults(defaults) -> List[str]:
    """把 ACTIONS 里的默认值解析成实际键列表（None = 平台相关）。"""
    if defaults is None:
        return multiline_default_keys()
    return list(defaults)


_ACTIONS_BY_ID: Dict[str, Tuple[str, str, str, str, List[str]]] = {
    a[0]: a for a in ACTIONS
}

# ── 键名校验 ──
_MODS = {"ctrl", "alt", "shift", "meta", "super", "hyper", "escape", "cmd"}
_KEYS = set("abcdefghijklmnopqrstuvwxyz0123456789") | {
    "enter", "return", "tab", "space", "backspace", "delete", "insert",
    "escape", "up", "down", "left", "right", "home", "end",
    "pageup", "pagedown", "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8",
    "f9", "f10", "f11", "f12", "space",
}
_COMBO_RE = re.compile(r"^[a-z0-9+_]+$")

_CONFIG_DIRNAME = os.path.join(".config", "onyx", "ai")
_CONFIG_NAME = "keymap.json"
_CONFIG_VERSION = 1

_HOME: Optional[str] = None
_OVERRIDES: Optional[Dict[str, List[str]]] = None


def init(user_home: str) -> None:
    """设置配置文件所在的主目录（AI 配置目录为 <home>/.config/onyx/ai）。"""
    global _HOME, _OVERRIDES
    if user_home and user_home != _HOME:
        _HOME = user_home
        _OVERRIDES = None      # 换 home → 重新加载


def config_path() -> Optional[str]:
    if not _HOME:
        return None
    return os.path.join(_HOME, _CONFIG_DIRNAME, _CONFIG_NAME)


def defaults() -> Dict[str, List[str]]:
    return {a[0]: _resolve_defaults(a[4]) for a in ACTIONS}


def normalize_combo(combo: str) -> Optional[str]:
    """把用户输入/捕获到的组合规整成标准键名；非法返回 None。

    支持多键序列（逗号分隔，如 `escape,enter`）；各段去重但保持书写顺序。
    """
    if not combo:
        return None
    raw = str(combo).strip().lower().replace(" ", "")
    if not raw:
        return None
    seq = [p for p in raw.split(",") if p]
    if not seq or len(seq) > 3:
        return None
    out: List[str] = []
    for part in seq:
        if not _COMBO_RE.match(part):
            return None
        segs = [p for p in part.split("+") if p]
        if not segs or len(segs) > 4:
            return None
        mods = segs[:-1]
        key = segs[-1]
        for m in mods:
            if m not in _MODS:
                return None
        if key not in _KEYS and len(key) != 1:
            return None
        # 去重但**保持用户书写顺序**（Textual 的 alt+ctrl+j 与 ctrl+alt+j 是不同绑定，
        # 归一化时不能擅自重排，否则默认键会被改写成另一个键）
        out.append("+".join(list(dict.fromkeys(mods)) + [key]))
    return ",".join(out)


def normalize_binding(combos) -> Optional[List[str]]:
    """规整一个动作的键列表（去重、去空）；非法或为空返回 None。"""
    if isinstance(combos, str):
        combos = [combos]
    if not combos:
        return None
    out: List[str] = []
    for c in combos:
        n = normalize_combo(c)
        if not n:
            return None
        if n not in out:
            out.append(n)
    return out or None


def _load() -> Dict[str, List[str]]:
    global _OVERRIDES
    if _OVERRIDES is not None:
        return _OVERRIDES
    data: Dict[str, List[str]] = {}
    path = config_path()
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f) or {}
            bindings = raw.get("bindings") if isinstance(raw, dict) else None
            if isinstance(bindings, dict):
                for aid, combos in bindings.items():
                    if aid not in _ACTIONS_BY_ID:
                        continue                      # 忽略未知/过期动作
                    norm = normalize_binding(combos)
                    if norm:
                        data[aid] = norm
        except Exception:
            data = {}                                  # 配置损坏 → 全用默认
    _OVERRIDES = data
    return _OVERRIDES


def _save() -> bool:
    path = config_path()
    if not path:
        return False
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {"version": _CONFIG_VERSION, "bindings": _load()}
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def combos(action_id: str) -> List[str]:
    """该动作当前生效的键列表（用户覆盖优先，否则默认）。"""
    spec = _ACTIONS_BY_ID.get(action_id)
    if not spec:
        return []
    return list(_load().get(action_id) or _resolve_defaults(spec[4]))


def all_combos() -> Dict[str, List[str]]:
    return {a[0]: combos(a[0]) for a in ACTIONS}


def is_customized(action_id: str) -> bool:
    return action_id in _load()


def conflicts() -> List[Tuple[str, List[str]]]:
    """返回被多个动作占用的键 → [(combo, [action_id, ...]), ...]。

    只比较**同一分组**内的动作：不同界面（TUI 主界面 / 对话模式）各自独立，
    同键互不影响。
    """
    seen: Dict[str, List[str]] = {}
    for a in ACTIONS:
        for c in combos(a[0]):
            seen.setdefault(c, [])
            if a[0] not in seen[c]:
                seen[c].append(a[0])
    out = []
    for c, ids in seen.items():
        if len(ids) < 2:
            continue
        groups = {group_of(i) for i in ids}
        if len(groups) == 1:
            out.append((c, ids))
    return out


def set_binding(action_id: str, combos_) -> Tuple[bool, str]:
    """设置某动作的键；返回 (是否成功, 失败原因)。"""
    if action_id not in _ACTIONS_BY_ID:
        return False, "unknown_action"
    norm = normalize_binding(combos_)
    if not norm:
        return False, "invalid_key"
    data = _load()
    data[action_id] = norm
    _OVERRIDES_SET(action_id, norm)
    if not _save():
        return False, "save_failed"
    return True, ""


def _OVERRIDES_SET(action_id: str, norm: List[str]) -> None:
    global _OVERRIDES
    if _OVERRIDES is None:
        _load()
    _OVERRIDES[action_id] = norm


def reset(action_id: str) -> bool:
    data = _load()
    if action_id in data:
        data.pop(action_id, None)
        return _save()
    return True


def reset_all() -> bool:
    global _OVERRIDES
    _OVERRIDES = {}
    return _save()


def label(action_id: str, lang: str = "chinese") -> str:
    spec = _ACTIONS_BY_ID.get(action_id)
    if not spec:
        return action_id
    return spec[3] if lang == "english" else spec[2]


def group_of(action_id: str) -> str:
    spec = _ACTIONS_BY_ID.get(action_id)
    return spec[1] if spec else "tui"


def pretty(action_id: str) -> str:
    """把键列表渲染成给人看的字符串，如 `alt+enter / shift+enter`。"""
    return " / ".join(combos(action_id))


# ── prompt_toolkit（AI 对话模式）键名转换 ──
_PTK_MOD = {"ctrl": "c", "alt": "a", "shift": "s", "meta": "m", "super": "s"}
_PTK_KEY = {
    "enter": "enter", "return": "enter", "tab": "tab", "space": "space",
    "escape": "escape", "backspace": "backspace", "delete": "delete",
    "insert": "insert", "up": "up", "down": "down", "left": "left",
    "right": "right", "home": "home", "end": "end",
    "pageup": "pageup", "pagedown": "pagedown",
}


def to_ptk(combo: str) -> List[str]:
    """把注册表键名转成 prompt_toolkit 的 `kb.add(*keys)` 形式。

    `ctrl+r` → ['c-r']；`escape,enter` → ['escape', 'enter']；无法转换返回 []。
    """
    parts = [p.strip() for p in str(combo).split(",") if p.strip()]
    if not parts:
        return []
    out: List[str] = []
    for part in parts:
        segs = [s for s in part.split("+") if s]
        if not segs:
            return []
        key = segs[-1]
        mods = segs[:-1]
        if key in _PTK_KEY:
            k = _PTK_KEY[key]
        elif len(key) == 1:
            k = key
        elif key.startswith("f") and key[1:].isdigit():
            k = key
        else:
            return []
        if mods:
            prefix = "".join(_PTK_MOD.get(m, "") for m in mods)
            if not prefix:
                return []
            k = f"{prefix}-{k}"
        out.append(k)
    return out
