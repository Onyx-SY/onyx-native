# -*- coding: utf-8 -*-
"""step-4：config-onyx-repl keys 子命令 + TUI 主 REPL 按键栏。"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "bin", "repl_config.py")
s = io.open(P, encoding="utf-8").read()

if "def repl_key_actions(" in s:
    print("SKIP：已存在")
    sys.exit(0)

# ── 1) 主 REPL 键位读写（插在「TUI 配置界面」段之前）──
HELPERS = '''# ────────────────────── 主 REPL 键位（ptk.json 的 key_bindings）──────────────────────

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


def read_repl_keys() -> Dict[str, str]:
    """当前主 REPL 键位（缺失项回落到默认表）。"""
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
                cur[k] = v.strip()
    return cur


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
        norm = str(combo or "").strip()
        if not norm:
            return False, "invalid_key"
        value = norm
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
    return set_repl_key(action, str(defaults[action]).replace(",", ","))


def reset_all_repl_keys() -> int:
    n = 0
    try:
        from lib.terminal.com import DEFAULT_PTK_CONFIG
        defaults = DEFAULT_PTK_CONFIG.get("key_bindings") or {}
    except Exception:
        return 0
    for action in list(defaults.keys()):
        if set_repl_key(action, str(defaults[action])):
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


'''
ANCHOR = "# ────────────────────────────── TUI 配置界面 ──────────────────────────────"
assert s.count(ANCHOR) == 1
s = s.replace(ANCHOR, HELPERS + ANCHOR, 1)

# ── 2) usage 文案补 keys ──
OLD_U_CN = '''  reset <项>            恢复某项默认
  reset --all           恢复全部默认
可配置项：'''
NEW_U_CN = '''  reset <项>            恢复某项默认
  reset --all           恢复全部默认
  keys                  列出主 REPL 键位（可在 TUI 里按一下键直接改）
  keys set  <动作> <键> 改主 REPL 键位（写回 ~/.config/onyx/ptk.json）
  keys reset <动作>|--all   恢复主 REPL 键位默认
可配置项：'''
OLD_U_EN = '''  reset <item>          reset one setting
  reset --all           reset everything
Settings: '''
NEW_U_EN = '''  reset <item>          reset one setting
  reset --all           reset everything
  keys                  list main-REPL key bindings (rebind by pressing a key in the TUI)
  keys set  <action> <key>   rebind a main-REPL key (writes ~/.config/onyx/ptk.json)
  keys reset <action>|--all  reset main-REPL key bindings
Settings: '''
for old, new, tag in ((OLD_U_CN, NEW_U_CN, "usage_cn"), (OLD_U_EN, NEW_U_EN, "usage_en")):
    if new in s:
        print(f"SKIP {tag}")
        continue
    assert s.count(old) == 1, tag
    s = s.replace(old, new, 1)
    print(f"OK   {tag}")

# ── 3) keys 子命令处理（插在 reset 分支之前）──
OLD_RESET = '''    if sub == "reset":
        if not rest:'''
NEW_RESET = '''    if sub == "keys":
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
                print(Fore.LIGHTBLACK_EX + _msg("（重开 Onyx 或新起一次输入后生效）",
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
        if not rest:'''
assert s.count(OLD_RESET) == 1
s = s.replace(OLD_RESET, NEW_RESET, 1)
print("OK   keys 子命令")

tmp = P + ".tmp"
io.open(tmp, "w", encoding="utf-8").write(s)
os.replace(tmp, P)
print("已写回 repl_config.py")
