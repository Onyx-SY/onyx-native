# -*- coding: utf-8 -*-
"""bin/repl_config.py — 主 REPL 统一配置入口（内置命令 `config-onyx-repl`）。

用法：
    config-onyx-repl                    打开 TUI 配置界面（默认行为）
    config-onyx-repl ui                 同上
    config-onyx-repl list               列出全部配置项与当前值
    config-onyx-repl get  <项>          读取某项
    config-onyx-repl set  <项> <值>     设置某项
    config-onyx-repl reset <项>         恢复某项默认
    config-onyx-repl reset --all        恢复全部默认

设计：
  · 与既有 `manage set` **同一套 JSON 配置**（etc/config.json 与 <home>/.config/onyx/*），
    不引入第二套配置源，避免两边不一致；
  · 配置项集中登记在 SETTINGS（类型 / 可选值 / 默认值 / 双语说明 / 是否需重启）；
  · 参数式与 TUI 界面共用同一套读写函数，行为一致。
"""
import json
import os
import sys
from typing import Any, Callable, Dict, List, Optional

try:
    import getpass as _getpass
    USER = _getpass.getuser()
except Exception:
    USER = os.environ.get("USER") or os.environ.get("LOGNAME") or "default"

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
USER_HOME_DIR = (os.path.join(ROOT_DIR, "root") if USER == "root"
                 else os.path.join(ROOT_DIR, "home", USER))
CONFIG_DIR = os.path.join(USER_HOME_DIR, ".config", "onyx")
CONFIG_JSON_PATH = os.path.join(ROOT_DIR, "onyx", "etc", "config.json")
SANDBOX_CONFIG_PATH = os.path.join(ROOT_DIR, "etc", "onyx", "sandbox")

# 文件式配置项（与 bin/manage.py 的 DEFAULT_CONFIGS 一一对应）
_FILE_SETTINGS: Dict[str, str] = {
    "debug-times": os.path.join(CONFIG_DIR, "debug-times"),
    "debug-parsecmd": os.path.join(CONFIG_DIR, "debug-parsecmd"),
    "language": os.path.join(CONFIG_DIR, "language"),
    "clean-log-time": os.path.join(CONFIG_DIR, "clean-log-time"),
    "adv_danger_cmd_prompt": os.path.join(CONFIG_DIR, "adv_danger_cmd_prompt"),
    "mcp": os.path.join(CONFIG_DIR, "mcp_enabled"),
    "spring-mode": os.path.join(CONFIG_DIR, "spring_mode"),
    "builtin-adv-syntax": os.path.join(CONFIG_DIR, "builtin-adv-syntax"),
}

# 配置项注册表：id / 类型 / 可选值 / 默认 / 说明 / 是否需重启
#   kind: bool | int | int_or_false | choice | choice_dyn
SETTINGS: List[Dict[str, Any]] = [
    dict(id="language", kind="choice", choices=["chinese", "english"],
         default="english", restart=True,
         cn="界面语言", en="UI language",
         cn_help="Chinese / English（也接受短码 zh/cn/en）",
         en_help="Chinese / English (short codes zh/cn/en accepted)"),
    dict(id="debug-times", kind="bool", default="false", restart=False,
         cn="命令耗时输出", en="Show command timing",
         cn_help="true = 每条命令后打印耗时", en_help="true = print elapsed time per command"),
    dict(id="debug-parsecmd", kind="bool", default="false", restart=False,
         cn="命令解析调试", en="Command parse debug",
         cn_help="true = 打印解析过程", en_help="true = print parse trace"),
    dict(id="clean-log-time", kind="int_or_false", default="3", restart=False,
         cn="日志保留天数", en="Log retention (days)",
         cn_help="正整数（天）或 false（不自动清理）",
         en_help="positive integer (days) or false (never clean)"),
    dict(id="adv_danger_cmd_prompt", kind="bool", default="true", restart=False,
         cn="adv 危险命令二次确认", en="adv dangerous-cmd confirm",
         cn_help="true = adv 模式执行危险命令前再确认一次",
         en_help="true = ask once more before dangerous cmds in adv mode"),
    dict(id="mcp", kind="bool", default="true", restart=True,
         cn="MCP 协议工具", en="MCP tools",
         cn_help="true = 启用 MCP 工具", en_help="true = enable MCP tools"),
    dict(id="spring-mode", kind="bool", default="true", restart=False,
         cn="启动问候语", en="Startup greeting",
         cn_help="true = 启动时显示问候语", en_help="true = show greeting on startup"),
    dict(id="builtin-adv-syntax", kind="bool", default="false", restart=False,
         cn="内置命令高级语法", en="Advanced syntax for builtins",
         cn_help="true = 允许内置命令使用管道/重定向等高级语法",
         en_help="true = allow pipes/redirection on builtin commands"),
    dict(id="sandbox", kind="bool", default="true", restart=True,
         cn="AI 沙箱", en="AI sandbox",
         cn_help="true = 启用 AI 沙箱限制", en_help="true = enable AI sandbox"),
    dict(id="prompt-style", kind="choice_dyn", choices_from="prompt_styles",
         default="onyx", restart=True,
         cn="提示符样式", en="Prompt style",
         cn_help="config.json 里 display_info.command_prompts 的键",
         en_help="a key of display_info.command_prompts in config.json"),
    dict(id="history-len", kind="int", default="10000", restart=True,
         cn="历史记录条数上限", en="History length limit",
         cn_help="正整数", en_help="positive integer"),
]

_SETTINGS_BY_ID: Dict[str, Dict[str, Any]] = {s["id"]: s for s in SETTINGS}

# config.json 里的配置项：id → (点分路径, 顶层键)
_JSON_SETTINGS = {
    "prompt-style": ("system_info.current_prompt_type", "system_info"),
    "history-len": ("system_info.max_history_len", "system_info"),
}


# ────────────────────────────── 基础读写 ──────────────────────────────

def _ensure_config_dir() -> None:
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
    except Exception:
        pass


def _read_text(path: str, default: str = "") -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return default


def _write_text(path: str, value: str) -> bool:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(str(value))
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def _read_json(path: str, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def _write_json(path: str, data: Any) -> bool:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def _json_get(dotted: str, default: Any = None) -> Any:
    node: Any = _read_json(CONFIG_JSON_PATH, {})
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def _json_set(dotted: str, value: Any) -> bool:
    data = _read_json(CONFIG_JSON_PATH, {})
    if not isinstance(data, dict):
        return False
    node = data
    parts = dotted.split(".")
    for part in parts[:-1]:
        if not isinstance(node.get(part), dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value
    return _write_json(CONFIG_JSON_PATH, data)


def _dynamic_choices(setting: Dict[str, Any]) -> List[str]:
    if setting.get("choices_from") == "prompt_styles":
        prompts = ((_read_json(CONFIG_JSON_PATH, {}) or {}).get("display_info") or {}) \
            .get("command_prompts") or {}
        return list(prompts.keys())
    return []


def choices_of(setting_id: str) -> List[str]:
    """该配置项的可选值（非枚举返回空列表）。"""
    s = _SETTINGS_BY_ID.get(setting_id)
    if not s:
        return []
    kind = s["kind"]
    if kind == "bool":
        return ["true", "false"]
    if kind == "choice":
        return list(s["choices"])
    if kind == "choice_dyn":
        return _dynamic_choices(s)
    return []


def normalize_value(setting_id: str, raw: str) -> Optional[str]:
    """把用户输入规整成落盘值；非法返回 None。"""
    s = _SETTINGS_BY_ID.get(setting_id)
    if not s:
        return None
    v = str(raw).strip()
    kind = s["kind"]
    if kind == "bool":
        low = v.lower()
        if low in ("1", "true", "on", "yes", "y", "开", "是"):
            return "true"
        if low in ("0", "false", "off", "no", "n", "关", "否"):
            return "false"
        return None
    if kind == "int":
        return v if v.isdigit() and int(v) > 0 else None
    if kind == "int_or_false":
        if v.lower() == "false":
            return "false"
        return v if v.isdigit() and int(v) > 0 else None
    if kind in ("choice", "choice_dyn"):
        opts = choices_of(setting_id)
        low = v.lower()
        if setting_id == "language":
            alias = {"zh": "chinese", "cn": "chinese", "中文": "chinese",
                     "en": "english", "英文": "english"}
            low = alias.get(low, low)
        for o in opts:
            if o.lower() == low:
                return o
        return None
    return v or None


def get_value(setting_id: str) -> Optional[str]:
    """读取当前值（未设置则回退默认）。"""
    s = _SETTINGS_BY_ID.get(setting_id)
    if not s:
        return None
    if setting_id in _JSON_SETTINGS:
        dotted, _top = _JSON_SETTINGS[setting_id]
        v = _json_get(dotted, None)
        return str(v) if v is not None else s["default"]
    if setting_id == "sandbox":
        return _read_text(SANDBOX_CONFIG_PATH, "true") or "true"
    path = _FILE_SETTINGS.get(setting_id)
    if not path:
        return s["default"]
    return _read_text(path, s["default"]) or s["default"]


def set_value(setting_id: str, raw: str) -> tuple:
    """写入配置；返回 (是否成功, 错误码/说明)。"""
    s = _SETTINGS_BY_ID.get(setting_id)
    if not s:
        return False, "unknown_setting"
    norm = normalize_value(setting_id, raw)
    if norm is None:
        return False, "invalid_value"
    if setting_id in _JSON_SETTINGS:
        dotted, _top = _JSON_SETTINGS[setting_id]
        val: Any = int(norm) if s["kind"] == "int" else norm
        return _json_set(dotted, val), ""
    if setting_id == "sandbox":
        return _write_text(SANDBOX_CONFIG_PATH, norm), ""
    path = _FILE_SETTINGS.get(setting_id)
    if not path:
        return False, "no_path"
    ok = _write_text(path, norm)
    if ok and setting_id == "language":
        # 同步 config.json 里的 language（与 manage set language 行为一致）
        cfg = _read_json(CONFIG_JSON_PATH, {})
        try:
            cfg.setdefault("display_info", {}).setdefault("language", {})["current"] = norm
            _write_json(CONFIG_JSON_PATH, cfg)
        except Exception:
            pass
    return ok, ""


def reset_value(setting_id: str) -> bool:
    s = _SETTINGS_BY_ID.get(setting_id)
    if not s:
        return False
    return set_value(setting_id, s["default"])[0]


def reset_all() -> int:
    n = 0
    for s in SETTINGS:
        if reset_value(s["id"]):
            n += 1
    return n


# ────────────────────────────── 输出/语言 ──────────────────────────────

def current_lang() -> str:
    """当前界面语言（chinese / english）。"""
    try:
        from bin.manage import get_current_language
        return (get_current_language() or "chinese").lower()
    except Exception:
        v = _read_text(_FILE_SETTINGS["language"], "english").lower()
        return "chinese" if v in ("chinese", "zh", "cn") else "english"


def _msg(cn: str, en: str) -> str:
    return cn if current_lang().startswith("chi") or current_lang() in ("zh", "cn") \
        else en


def label_of(setting: Dict[str, Any], lang: Optional[str] = None) -> str:
    lang = (lang or current_lang()).lower()
    return setting["en"] if lang.startswith("eng") or lang in ("en",) else setting["cn"]


def help_of(setting: Dict[str, Any], lang: Optional[str] = None) -> str:
    lang = (lang or current_lang()).lower()
    return setting["en_help"] if lang.startswith("eng") or lang in ("en",) \
        else setting["cn_help"]


def format_list(lang: Optional[str] = None) -> List[str]:
    """`config-onyx-repl list` 的输出行。"""
    lines = []
    for s in SETTINGS:
        val = get_value(s["id"])
        mark = "*" if val != s["default"] else " "
        opts = choices_of(s["id"])
        extra = f"  ({'/'.join(opts)})" if opts else ""
        lines.append(f" {mark} {s['id']:<22} = {str(val):<12}{extra}  {label_of(s, lang)}")
    lines.append(_msg("（* = 已改动，默认值见 reset）", "(* = changed; see `reset` for defaults)"))
    return lines


# ────────────────────── 主 REPL 键位（ptk.json 的 key_bindings）──────────────────────

def repl_key_actions() -> List[tuple]:
    """主 REPL 可配置动作表：[(动作 id, ptk.json 键名, 中文, English), ...]。"""
    try:
        from lib.terminal.kb import REPL_KEY_ACTIONS
        return list(REPL_KEY_ACTIONS)
    except Exception:
        return []


def _ptk_path() -> str:
    try:
        from lib.terminal.com import PTK_CONFIG_PATH
        return os.path.expanduser(PTK_CONFIG_PATH)
    except Exception:
        return os.path.join(CONFIG_DIR, "ptk.json")


def _read_ptk() -> Dict[str, Any]:
    try:
        from lib.terminal.com import load_ptk_config
        cfg = load_ptk_config() or {}
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return _read_json(_ptk_path(), {}) or {}


def _norm_disp(value: str) -> str:
    """统一键位显示形式：`escape, enter` → `escape,enter`（默认表与用户值才可比）。"""
    return ",".join(p.strip() for p in str(value or "").split(",") if p.strip())


def read_repl_keys() -> Dict[str, str]:
    """当前主 REPL 键位（缺失项回落到默认表；统一去逗号后空格）。"""
    defaults = {}
    try:
        from lib.terminal.com import DEFAULT_PTK_CONFIG
        defaults = dict((DEFAULT_PTK_CONFIG.get("key_bindings") or {}))
    except Exception:
        pass
    cur = dict(defaults)
    kb_cfg = (_read_ptk().get("key_bindings") or {})
    if isinstance(kb_cfg, dict):
        for k, v in kb_cfg.items():
            if isinstance(v, str) and v.strip():
                cur[k] = _norm_disp(v)
    for k in list(cur.keys()):
        cur[k] = _norm_disp(cur[k])
    return cur


def _valid_ptk(value: str) -> bool:
    """粗略校验 prompt_toolkit 风格键序列：c-r / s-tab / escape,up / pageup / f2。"""
    import re as _re
    parts = [p.strip() for p in str(value or "").split(",") if p.strip()]
    if not parts or len(parts) > 3:
        return False
    return all(_re.match(r"^(c-|a-|s-|m-)?[a-z0-9][a-z0-9-]*$", p) for p in parts)


def _write_ptk_key(action: str, value: str) -> bool:
    """把某个动作的键直接写进 ptk.json（不做风格转换，供恢复默认等已知合法值使用）。"""
    path = _ptk_path()
    cfg = _read_json(path, {}) or {}
    if not isinstance(cfg, dict):
        cfg = {}
    kb_cfg = cfg.get("key_bindings")
    if not isinstance(kb_cfg, dict):
        kb_cfg = {}
    kb_cfg[action] = ",".join(p.strip() for p in str(value).split(",") if p.strip())
    cfg["key_bindings"] = kb_cfg
    return _write_json(path, cfg)


def set_repl_key(action: str, combo: str) -> tuple:
    """设置主 REPL 某个动作的键（写回 ptk.json）；返回 (是否成功, 错误码)。"""
    known = {a[0] for a in repl_key_actions()}
    if action not in known:
        return False, "unknown_action"
    try:
        from bin.ai_lib import keymap as _km
        norm = _km.normalize_combo(combo)
        if not norm:
            return False, "invalid_key"
        keys = _km.to_ptk(norm)
        if not keys:
            return False, "invalid_key"
        value = ",".join(keys)
    except Exception:
        norm = ""
    if not norm:
        # 兼容直接写 ptk 风格（c-l / s-tab / escape,up）——恢复默认值走的就是这条路
        raw = str(combo or "").strip()
        if not _valid_ptk(raw):
            return False, "invalid_key"
        value = ",".join(p.strip() for p in raw.split(",") if p.strip())
    path = _ptk_path()
    cfg = _read_json(path, {}) or {}
    if not isinstance(cfg, dict):
        cfg = {}
    kb_cfg = cfg.get("key_bindings")
    if not isinstance(kb_cfg, dict):
        kb_cfg = {}
    kb_cfg[action] = value
    cfg["key_bindings"] = kb_cfg
    return _write_json(path, cfg), ""


def reset_repl_key(action: str) -> bool:
    """恢复某个主 REPL 键位的默认值（写回默认表里的值）。"""
    try:
        from lib.terminal.com import DEFAULT_PTK_CONFIG
        defaults = DEFAULT_PTK_CONFIG.get("key_bindings") or {}
    except Exception:
        defaults = {}
    if action not in defaults:
        return False
    return _write_ptk_key(action, str(defaults[action]))


def reset_all_repl_keys() -> int:
    n = 0
    try:
        from lib.terminal.com import DEFAULT_PTK_CONFIG
        defaults = DEFAULT_PTK_CONFIG.get("key_bindings") or {}
    except Exception:
        return 0
    for action in list(defaults.keys()):
        if _write_ptk_key(action, str(defaults[action])):
            n += 1
    return n


def repl_key_lines(lang: Optional[str] = None) -> List[str]:
    """`config-onyx-repl keys` 的输出行。"""
    cur = read_repl_keys()
    try:
        from lib.terminal.com import DEFAULT_PTK_CONFIG
        defaults = DEFAULT_PTK_CONFIG.get("key_bindings") or {}
    except Exception:
        defaults = {}
    lines = []
    for aid, pkey, cn, en in repl_key_actions():
        val = cur.get(pkey, "")
        mark = "*" if defaults.get(pkey) not in (None, val) else " "
        lines.append(f" {mark} {pkey:<22} = {val:<16} {en if _is_en(lang) else cn}")
    lines.append(_msg("（* = 已改动；配置文件 ~/.config/onyx/ptk.json）",
                      "(* = changed; config: ~/.config/onyx/ptk.json)"))
    return lines


def _is_en(lang: Optional[str]) -> bool:
    return str(lang or current_lang()).lower().startswith("eng")


# ────────────────────────────── TUI 配置界面 ──────────────────────────────

_TUI_CSS = """
Screen { background: $surface; }
#cfg-title { dock: top; height: 1; padding: 0 1; background: $panel; color: $text; }
#cfg-items { height: 1fr; border: round $primary; margin: 0 1; }
#cfg-hint { dock: bottom; height: 1; padding: 0 1; color: $text-muted; }
#box { width: 70%; height: auto; max-height: 80%; padding: 1 2;
       background: $panel; border: round $primary; align: center middle; }
#box-title { height: auto; color: $text; }
#box-help { height: auto; color: $text-muted; padding: 1 0; }
#box-hint { height: 1; color: $text-muted; padding-top: 1; }
"""


def build_tui_app(lang: Optional[str] = None):
    """构造 TUI 配置界面的 App 类（供 run_tui 与无头测试使用）。

    Textual 不可用 / 构造失败 → None。
    """
    try:
        from textual.app import App, ComposeResult
        from textual.binding import Binding
        from textual.containers import Vertical
        from textual.screen import ModalScreen
        from textual.widgets import Input, OptionList, Static
        from textual.widgets.option_list import Option
    except Exception:
        return None

    lang = (lang or current_lang()).lower()

    class _Edit(ModalScreen):
        BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

        def __init__(self, setting):
            super().__init__()
            self._s = setting

        def compose(self):
            opts = choices_of(self._s["id"])
            with Vertical(id="box"):
                yield Static(f"{self._s['id']} — {label_of(self._s, lang)}", id="box-title")
                yield Static(help_of(self._s, lang), id="box-help")
                if opts:
                    yield OptionList(*opts, id="vals")
                else:
                    yield Input(value=str(get_value(self._s["id"]) or ""),
                                placeholder="new value", id="val")
                yield Static(_msg("Enter 保存 · Esc 取消", "Enter save · Esc cancel"),
                             id="box-hint")

        def on_mount(self):
            try:
                self.query_one("#vals", OptionList).focus()
            except Exception:
                try:
                    self.query_one("#val", Input).focus()
                except Exception:
                    pass

        def on_option_list_option_selected(self, event):
            self.dismiss(str(event.option.prompt))

        def on_input_submitted(self, event):
            self.dismiss(event.value)

    class _Capture(ModalScreen):
        """按一下要绑定的键（Esc 取消）。"""

        def compose(self):
            with Vertical(id="box"):
                yield Static(_msg("⌨️ 按下要绑定的按键…", "⌨️ Press the key to bind…"),
                             id="box-title")
                yield Static(_msg("（Esc 取消；直接按你想用的那个键）",
                                  "(Esc to cancel; just press the key you want)"),
                             id="box-help")

        def on_mount(self):
            try:
                self.focus()
            except Exception:
                pass

        def on_key(self, event):
            k = getattr(event, "key", "") or ""
            if not k:
                return
            if k in ("escape", "ctrl+c", "ctrl+q"):
                self.dismiss(None)
                return
            if k in ("shift", "ctrl", "alt", "meta", "super"):
                return
            self.dismiss(k)

    class _KeysScreen(ModalScreen):
        """主 REPL 键位列表：Enter 按新键绑定，r 恢复该项，R 恢复全部。"""

        BINDINGS = [Binding("escape", "back", "Back", show=False),
                    Binding("r", "reset_one", "Reset", show=False),
                    Binding("R", "reset_all", "Reset all", show=False)]

        def compose(self):
            yield Static(_msg("⌨️ 主 REPL 按键（写回 ~/.config/onyx/ptk.json）",
                              "⌨️ Main REPL keys (writes ~/.config/onyx/ptk.json)"),
                         id="cfg-title")
            yield OptionList(*self._options(), id="cfg-items")
            yield Static(_msg("↑↓ 选择 · Enter 按新键绑定 · r 恢复该项 · R 恢复全部 · Esc 返回",
                              "↑↓ move · Enter rebind · r reset · R reset all · Esc back"),
                         id="cfg-hint")

        def _options(self):
            cur = read_repl_keys()
            out = []
            for aid, pkey, cn, en in repl_key_actions():
                label = en if _is_en(lang) else cn
                out.append(Option(f" {pkey:<22} = {str(cur.get(pkey, '')):<16} {label}"))
            return out

        def on_mount(self):
            try:
                self.query_one("#cfg-items", OptionList).focus()
            except Exception:
                pass

        def _refresh(self):
            ol = self.query_one("#cfg-items", OptionList)
            keep = ol.highlighted
            ol.clear_options()
            for o in self._options():
                ol.add_option(o)
            if keep is not None and ol.option_count:
                ol.highlighted = min(keep, ol.option_count - 1)

        def on_option_list_option_selected(self, event):
            acts = repl_key_actions()
            idx = event.option_index
            if idx is None or idx >= len(acts):
                return
            aid = acts[idx][0]

            def _done(key):
                if key:
                    set_repl_key(aid, key)
                self._refresh()

            self.push_screen(_Capture(), _done)

        def action_back(self):
            self.dismiss(None)

        def action_reset_one(self):
            acts = repl_key_actions()
            idx = self.query_one("#cfg-items", OptionList).highlighted
            if idx is None or idx >= len(acts):
                return
            reset_repl_key(acts[idx][0])
            self._refresh()

        def action_reset_all(self):
            reset_all_repl_keys()
            self._refresh()

    class _App(App):
        CSS = _TUI_CSS
        BINDINGS = [Binding("q", "quit", "Quit"), Binding("escape", "quit", "Quit"),
                    Binding("r", "reset_one", "Reset", show=False),
                    Binding("R", "reset_all", "Reset all", show=False)]

        def compose(self) -> ComposeResult:
            yield Static("⚙️  Onyx REPL Config", id="cfg-title")
            yield OptionList(*self._options(), id="cfg-items")
            yield Static(self._hint(), id="cfg-hint")

        def _options(self):
            out = []
            for s in SETTINGS:
                val = get_value(s["id"])
                mark = "*" if val != s["default"] else " "
                out.append(Option(f"{mark} {s['id']:<22} = {str(val):<12} {label_of(s, lang)}"))
            out.append(Option(_msg("⌨️ 主 REPL 按键（Enter 进入，按一下键即可改）",
                                   "⌨️ Main REPL keys (Enter to open, press a key to rebind)")))
            return out

        def _hint(self, extra: str = "") -> str:
            base = _msg("↑↓ 选择 · Enter 修改 · r 恢复该项默认 · R 恢复全部 · q 退出",
                        "↑↓ move · Enter edit · r reset item · R reset all · q quit")
            return f"{base}   {extra}".strip()

        def _refresh(self, extra: str = ""):
            ol = self.query_one("#cfg-items", OptionList)
            keep = ol.highlighted
            ol.clear_options()
            for o in self._options():
                ol.add_option(o)
            if keep is not None and ol.option_count:
                ol.highlighted = min(keep, ol.option_count - 1)
            self.query_one("#cfg-hint", Static).update(self._hint(extra))

        def on_mount(self):
            try:
                self.query_one("#cfg-items", OptionList).focus()
            except Exception:
                pass

        def on_option_list_option_selected(self, event):
            idx = event.option_index
            if idx is not None and idx == len(SETTINGS):
                self.push_screen(_KeysScreen(), lambda _v: self._refresh())
                return
            if idx is None or idx >= len(SETTINGS):
                return
            s = SETTINGS[idx]

            def _done(value):
                if value is None or str(value) == "":
                    return
                ok, err = set_value(s["id"], str(value))
                if ok:
                    extra = _msg(f"✅ {s['id']} = {get_value(s['id'])}",
                                 f"✅ {s['id']} = {get_value(s['id'])}")
                    if s.get("restart"):
                        extra += _msg("（重启后生效）", " (takes effect after restart)")
                else:
                    extra = _msg(f"❌ 无效值：{value}", f"❌ invalid value: {value}")
                self._refresh(extra)

            self.push_screen(_Edit(s), _done)

        def action_reset_one(self):
            ol = self.query_one("#cfg-items", OptionList)
            idx = ol.highlighted
            if idx is None or idx >= len(SETTINGS):
                return
            s = SETTINGS[idx]
            reset_value(s["id"])
            self._refresh(_msg(f"♻️ {s['id']} 已恢复默认", f"♻️ {s['id']} reset"))

        def action_reset_all(self):
            n = reset_all()
            self._refresh(_msg(f"♻️ 已恢复全部默认（{n} 项）", f"♻️ reset all ({n} items)"))

    return _App


def run_tui(lang: Optional[str] = None) -> int:
    """打开 TUI 配置界面。返回 0 正常退出；非 0 表示 Textual 不可用。"""
    cls = build_tui_app(lang)
    if cls is None:
        return 1
    try:
        cls().run()
        return 0
    except Exception:
        return 1


# ────────────────────────────── 内置命令入口 ──────────────────────────────

_USAGE_CN = """用法：config-onyx-repl [子命令]
  （无参数）            打开 TUI 配置界面
  ui                    打开 TUI 配置界面
  list                  列出全部配置项与当前值
  get  <项>             读取某项
  set  <项> <值>        设置某项（与 manage set 同一套配置）
  reset <项>            恢复某项默认
  reset --all           恢复全部默认
  keys                  列出主 REPL 键位（可在 TUI 里按一下键直接改）
  keys set  <动作> <键> 改主 REPL 键位（写回 ~/.config/onyx/ptk.json）
  keys reset <动作>|--all   恢复主 REPL 键位默认
可配置项：""" + "、".join(s["id"] for s in SETTINGS)

_USAGE_EN = """Usage: config-onyx-repl [subcommand]
  (no args)             open the TUI config screen
  ui                    open the TUI config screen
  list                  list all settings and current values
  get  <item>           read one setting
  set  <item> <value>   set one setting (same config as `manage set`)
  reset <item>          reset one setting
  reset --all           reset everything
  keys                  list main-REPL key bindings (rebind by pressing a key in the TUI)
  keys set  <action> <key>   rebind a main-REPL key (writes ~/.config/onyx/ptk.json)
  keys reset <action>|--all  reset main-REPL key bindings
Settings: """ + ", ".join(s["id"] for s in SETTINGS)


def handle_config_repl(cmd_parts: List[str], request_id: str = "") -> None:
    """内置命令 `config-onyx-repl` 的入口（签名对齐其它 handler）。"""
    from lib.terminal.colors import Fore, Style

    parts = list(cmd_parts or [])
    if parts and parts[0].endswith("config-onyx-repl"):
        parts = parts[1:]

    sub = (parts[0].lower() if parts else "ui")
    rest = parts[1:]

    if sub in ("-h", "--help", "help"):
        print(_msg(_USAGE_CN, _USAGE_EN))
        return

    if sub in ("ui", "tui", ""):
        rc = run_tui()
        if rc != 0:
            print(Fore.YELLOW + _msg(
                "⚠️ TUI 配置界面不可用（Textual 未安装或终端不支持），已回退到命令行用法：",
                "⚠️ TUI config unavailable (no Textual / unsupported terminal); falling back to CLI:"
            ) + Style.RESET_ALL)
            print(_msg(_USAGE_CN, _USAGE_EN))
        return

    if sub == "list":
        for line in format_list():
            print(line)
        return

    if sub == "get":
        if not rest:
            print(Fore.RED + _msg("用法：config-onyx-repl get <项>",
                                  "Usage: config-onyx-repl get <item>") + Style.RESET_ALL)
            return
        sid = rest[0]
        if sid not in _SETTINGS_BY_ID:
            print(Fore.RED + _msg(f"未知配置项：{sid}", f"Unknown setting: {sid}") + Style.RESET_ALL)
            return
        print(f"{sid} = {get_value(sid)}")
        return

    if sub == "set":
        if len(rest) < 2:
            print(Fore.RED + _msg("用法：config-onyx-repl set <项> <值>",
                                  "Usage: config-onyx-repl set <item> <value>") + Style.RESET_ALL)
            return
        sid, value = rest[0], " ".join(rest[1:])
        ok, err = set_value(sid, value)
        if ok:
            extra = _msg("（重启后生效）", " (takes effect after restart)") \
                if _SETTINGS_BY_ID.get(sid, {}).get("restart") else ""
            print(Fore.GREEN + _msg(f"✅ {sid} = {get_value(sid)}",
                                    f"✅ {sid} = {get_value(sid)}") + extra + Style.RESET_ALL)
        elif err == "unknown_setting":
            print(Fore.RED + _msg(f"未知配置项：{sid}", f"Unknown setting: {sid}") + Style.RESET_ALL)
        else:
            opts = choices_of(sid)
            hint = f"（可选：{'/'.join(opts)}）" if opts else ""
            print(Fore.RED + _msg(f"❌ 无效值：{value}{hint}",
                                  f"❌ invalid value: {value}") + Style.RESET_ALL)
        return

    if sub == "keys":
        acts = {a[0]: a for a in repl_key_actions()}
        if not rest or rest[0] in ("list", "-l"):
            for line in repl_key_lines():
                print(line)
            return
        sub2 = rest[0].lower()
        if sub2 == "set":
            if len(rest) < 3:
                print(Fore.RED + _msg("用法：config-onyx-repl keys set <动作> <键>",
                                      "Usage: config-onyx-repl keys set <action> <key>")
                      + Style.RESET_ALL)
                return
            aid, combo = rest[1], " ".join(rest[2:])
            ok, err = set_repl_key(aid, combo)
            if ok:
                print(Fore.GREEN + _msg(f"✅ {aid} = {read_repl_keys().get(aid, combo)}",
                                        f"✅ {aid} = {read_repl_keys().get(aid, combo)}")
                      + Style.RESET_ALL)
                print(Fore.LIGHTBLACK + _msg("（重开 Onyx 或新起一次输入后生效）",
                                                " (applies after restart / next prompt)")
                      + Style.RESET_ALL)
            elif err == "unknown_action":
                print(Fore.RED + _msg(f"未知动作：{aid}（可用：{'、'.join(acts)}）",
                                      f"Unknown action: {aid}") + Style.RESET_ALL)
            else:
                print(Fore.RED + _msg(f"❌ 无效按键：{combo}", f"❌ invalid key: {combo}")
                      + Style.RESET_ALL)
            return
        if sub2 == "reset":
            if len(rest) < 2:
                print(Fore.RED + _msg("用法：config-onyx-repl keys reset <动作>|--all",
                                      "Usage: config-onyx-repl keys reset <action>|--all")
                      + Style.RESET_ALL)
                return
            if rest[1] in ("--all", "-a", "all"):
                n = reset_all_repl_keys()
                print(Fore.GREEN + _msg(f"♻️ 已恢复全部主 REPL 键位默认（{n} 项）",
                                        f"♻️ reset all main-REPL keys ({n})") + Style.RESET_ALL)
                return
            aid = rest[1]
            if aid not in acts:
                print(Fore.RED + _msg(f"未知动作：{aid}", f"Unknown action: {aid}")
                      + Style.RESET_ALL)
                return
            reset_repl_key(aid)
            print(Fore.GREEN + _msg(f"♻️ {aid} = {read_repl_keys().get(aid, '')}",
                                    f"♻️ {aid} = {read_repl_keys().get(aid, '')}")
                  + Style.RESET_ALL)
            return
        print(Fore.RED + _msg(f"未知子命令：keys {sub2}", f"Unknown subcommand: keys {sub2}")
              + Style.RESET_ALL)
        return

    if sub == "reset":
        if not rest:
            print(Fore.RED + _msg("用法：config-onyx-repl reset <项>|--all",
                                  "Usage: config-onyx-repl reset <item>|--all") + Style.RESET_ALL)
            return
        if rest[0] in ("--all", "-a", "all"):
            n = reset_all()
            print(Fore.GREEN + _msg(f"♻️ 已恢复全部默认（{n} 项）",
                                    f"♻️ reset all ({n} items)") + Style.RESET_ALL)
            return
        sid = rest[0]
        if sid not in _SETTINGS_BY_ID:
            print(Fore.RED + _msg(f"未知配置项：{sid}", f"Unknown setting: {sid}") + Style.RESET_ALL)
            return
        reset_value(sid)
        print(Fore.GREEN + _msg(f"♻️ {sid} = {get_value(sid)}",
                                f"♻️ {sid} = {get_value(sid)}") + Style.RESET_ALL)
        return

    print(Fore.RED + _msg(f"未知子命令：{sub}", f"Unknown subcommand: {sub}") + Style.RESET_ALL)
    print(_msg(_USAGE_CN, _USAGE_EN))
