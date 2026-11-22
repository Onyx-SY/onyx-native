# -*- coding: utf-8 -*-
"""私人配置的非交互入口：`ai config <show|get|set|test|backup|restore|doctor|path>`。

为什么要它：原来只能在 REPL/TUI 里敲 /config 用方向键选，
手机上难操作，也不能脚本化；改完 key/URL 也无从验证。

设计约束：
- 不改配置文件格式（key.json 仍混淆存储，0600 原子写）
- 任何写入都走 config.save_key_conf / _atomic_write_json（自带 0600 + fsync）
- backup 目录 0700；restore 强制确认并自动做一次 pre-restore 备份
- 不打印明文 key（show/get 一律掩码）
"""
import json
import os
import shutil
import time
from typing import Any, Dict, List, Optional, Tuple

from . import config as _cfg

ACTIONS = ("show", "get", "set", "test", "backup", "restore", "doctor", "path")
BACKUP_ROOT = os.path.join(os.path.expanduser("~"), ".config", "onyx", "backups")


def _lang() -> str:
    try:
        from .config import get_current_lang
        return get_current_lang()
    except Exception:
        return "chinese"


def _t(cn: str, en: str) -> str:
    return cn if str(_lang()).lower().startswith(("chi", "zh")) else en


def _out(msg: str = "") -> None:
    print(msg)


# ══════════════════════════════════════════════════════════════════
# 路径
# ══════════════════════════════════════════════════════════════════

def config_paths() -> List[Tuple[str, str]]:
    """(说明, 路径)：全部用户私人配置的位置。"""
    key = _cfg.KEY_CONF_PATH
    base = os.path.dirname(os.path.dirname(key))          # ~/.config/onyx
    home = os.path.expanduser("~")
    paths = [
        (_t("AI 平台/模型/密钥/参数", "AI platform/model/key/params"), key),
        (_t("语言", "language"), os.path.join(base, "language")),
        (_t("MCP 服务器", "MCP servers"), os.path.join(base, "mcp", "mcp.json")),
        (_t("危险命令扩展", "extra dangerous cmds"),
         os.path.join(base, "ai_danger", "extra_dangerous_commands.txt")),
        (_t("感官/主动感知", "senses"), os.path.join(home, ".ai_s", "senses", "config.json")),
        (_t("插件注册索引", "plugin registry"),
         os.path.join(home, ".ai_s", "plugin_tool", "index.json")),
        (_t("花费统计", "cost stats"), os.path.join(home, ".ai_s", "cost.json")),
    ]
    return paths


def _mode_of(path: str) -> str:
    try:
        return oct(os.stat(path).st_mode & 0o777)
    except Exception:
        return "-"


def _size_of(path: str) -> str:
    try:
        n = os.path.getsize(path)
    except Exception:
        return "-"
    for unit in ("B", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return "%d %s" % (n, unit) if unit == "B" else "%.1f %s" % (n, unit)
        n /= 1024.0
    return str(n)


# ══════════════════════════════════════════════════════════════════
# show / get / path
# ══════════════════════════════════════════════════════════════════

def _summary() -> Dict[str, Any]:
    conf = _cfg.load_key_conf() or {}
    key = (conf.get("api_key") or "").strip()
    return {
        "platform": conf.get("platform") or "",
        "model": conf.get("model") or "",
        "api_url": conf.get("api_url") or "",
        "params": conf.get("params") or {},
        "key": _cfg.mask_api_key(key) if key else "",
        "key_ok": bool(key),
        "key_file": _cfg.KEY_CONF_PATH,
        "key_file_mode": _mode_of(_cfg.KEY_CONF_PATH),
        "backup_dir": BACKUP_ROOT,
    }


def cmd_show(args: List[str]) -> int:
    s = _summary()
    if "--json" in args:
        _out(json.dumps(s, ensure_ascii=False, indent=2))
        return 0
    plat = s["platform"] or _t("（未配置）", "(not configured)")
    name = ""
    try:
        name = (_cfg._SUPPORTED_PLATFORMS.get(s["platform"]) or {}).get("name", "")
    except Exception:
        pass
    params = ", ".join("%s=%s" % (k, v) for k, v in sorted(s["params"].items())) or "-"
    _out("")
    _out(_t("  🔑 私人配置 / AI 配置", "  🔑 Personal config / AI config"))
    _out("  " + "─" * 46)
    _out("  %-12s %s%s" % (_t("平台", "Platform"), plat,
                           (" (%s)" % name) if name and name != plat else ""))
    _out("  %-12s %s" % (_t("模型", "Model"), s["model"] or "-"))
    _out("  %-12s %s" % ("API URL", s["api_url"] or _t("（平台默认）", "(platform default)")))
    _out("  %-12s %s" % (_t("参数", "Params"), params))
    _out("  %-12s %s" % (_t("密钥", "API key"), s["key"] or _t("未设置", "not set")))
    _out("  %-12s %s  (%s)" % (_t("配置文件", "Config file"), s["key_file"],
                               _t("权限 %s" % s["key_file_mode"], "mode %s" % s["key_file_mode"])))
    _out("  %-12s %s" % (_t("备份目录", "Backups"), s["backup_dir"]))
    _out("")
    if not s["key_ok"]:
        _out(_t("  ⚠️ 还没有配置密钥：运行 `ai config set key=<你的 API Key>`",
                "  ⚠️ no API key yet: run `ai config set key=<your key>`"))
    return 0


def cmd_get(args: List[str]) -> int:
    if not args:
        _out(_t("用法：ai config get <platform|model|key|url|param.名字>",
                "Usage: ai config get <platform|model|key|url|param.NAME>"))
        return 1
    s = _summary()
    name = args[0]
    if name == "platform":
        _out(s["platform"])
    elif name == "model":
        _out(s["model"])
    elif name == "key":
        _out(s["key"] or "")
    elif name in ("url", "api_url"):
        _out(s["api_url"])
    elif name.startswith("param."):
        _out(str(s["params"].get(name[6:], "")))
    else:
        _out(_t("❌ 未知键：%s" % name, "❌ unknown key: %s" % name))
        return 1
    return 0


def cmd_path(args: List[str]) -> int:
    for label, p in config_paths():
        mark = "✅" if os.path.exists(p) else "·"
        _out("%s %-28s %s" % (mark, label, p))
    return 0


# ══════════════════════════════════════════════════════════════════
# set
# ══════════════════════════════════════════════════════════════════

def _coerce(v: str) -> Any:
    low = v.strip().lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(v)
    except Exception:
        pass
    try:
        return float(v)
    except Exception:
        return v


def cmd_set(args: List[str]) -> int:
    if not args:
        _out(_t("用法：ai config set platform=<名> model=<名> key=<密钥> url=<地址> param.temperature=0.7",
                "Usage: ai config set platform=<n> model=<n> key=<k> url=<u> param.temperature=0.7"))
        return 1
    conf = _cfg.load_key_conf() or {}
    platform = conf.get("platform") or "deepseek"
    model = conf.get("model") or ""
    api_key = (conf.get("api_key") or "").strip()
    api_url = conf.get("api_url") or ""
    params = dict(conf.get("params") or {})

    changed = []
    for pair in args:
        if "=" not in pair:
            _out(_t("❌ 格式错误（需要 k=v）：%s" % pair, "❌ expected k=v: %s" % pair))
            return 1
        k, v = pair.split("=", 1)
        k = k.strip()
        v = v.strip()
        if k == "platform":
            if v not in _cfg._SUPPORTED_PLATFORMS and v != "custom":
                _out(_t("❌ 未知平台：%s（可用：%s, custom）"
                        % (v, ", ".join(_cfg._SUPPORTED_PLATFORMS)),
                        "❌ unknown platform: %s (available: %s, custom)"
                        % (v, ", ".join(_cfg._SUPPORTED_PLATFORMS))))
                return 1
            platform = v
            info = _cfg._SUPPORTED_PLATFORMS.get(v) or {}
            if info:
                # 换平台后旧模型多半不可用 → 切到新平台默认；参数则保留用户值
                model = info.get("default_model") or model
                # 只补缺失的默认参数，保留用户调过的值
                merged = dict(info.get("params") or {})
                merged.update(params)
                params = merged
            if v != "custom":
                api_url = ""      # 回到平台默认地址
            changed.append("platform")
        elif k == "model":
            if not v:
                _out(_t("❌ model 不能为空", "❌ model must not be empty"))
                return 1
            model = v
            changed.append("model")
        elif k in ("key", "api_key"):
            if not _cfg._valid_api_key(v):
                _out(_t("❌ 密钥格式无效：长度需 ≥ 8，且不含空格/换行",
                        "❌ invalid key: ≥ 8 chars, no spaces/newlines"))
                return 1
            api_key = v
            changed.append("key")
        elif k in ("url", "api_url"):
            if not (v.startswith("http://") or v.startswith("https://")):
                _out(_t("❌ URL 必须以 http:// 或 https:// 开头", "❌ URL must start with http(s)://"))
                return 1
            api_url = v
            changed.append("url")
        elif k.startswith("param."):
            pname = k[6:]
            if not pname:
                _out(_t("❌ 参数名不能为空", "❌ empty param name"))
                return 1
            params[pname] = _coerce(v)
            changed.append(k)
        else:
            _out(_t("❌ 未知键：%s（可用：platform/model/key/url/param.<名字>）" % k,
                    "❌ unknown key: %s (use platform/model/key/url/param.<name>)" % k))
            return 1

    if not api_key:
        _out(_t("❌ 还没有 API Key，请同时给 key=<你的密钥>",
                "❌ no API key yet; also pass key=<your key>"))
        return 1

    _cfg.save_key_conf(platform, api_key, model, params, api_url)
    _out(_t("✅ 已保存：%s" % ", ".join(changed), "✅ saved: %s" % ", ".join(changed)))
    _out(_t("   %s（权限 %s）" % (_cfg.KEY_CONF_PATH, _mode_of(_cfg.KEY_CONF_PATH)),
           "   %s (mode %s)" % (_cfg.KEY_CONF_PATH, _mode_of(_cfg.KEY_CONF_PATH))))
    if "key" in changed or "url" in changed:
        _out(_t("   提示：`ai config test` 可立刻验证连通性",
                "   Tip: run `ai config test` to verify connectivity"))
    return 0


# ══════════════════════════════════════════════════════════════════
# test（最小请求）
# ══════════════════════════════════════════════════════════════════

def _resolve_url(conf: Dict[str, Any]) -> str:
    plat = conf.get("platform") or "deepseek"
    if conf.get("api_url"):
        return str(conf["api_url"])
    info = _cfg._SUPPORTED_PLATFORMS.get(plat) or {}
    return str(info.get("api_url") or "https://api.openai.com/v1/chat/completions")


def test_connection(timeout: int = 15) -> Tuple[bool, str]:
    """发一个 max_tokens=1 的最小请求。返回 (是否可用, 说明)。"""
    import urllib.error
    import urllib.request

    conf = _cfg.load_key_conf() or {}
    key = (conf.get("api_key") or "").strip()
    if not key:
        return False, _t("未配置 API Key", "no API key configured")
    model = conf.get("model") or ""
    plat = conf.get("platform") or ""
    info = _cfg._SUPPORTED_PLATFORMS.get(plat) or {}
    fmt = str(info.get("stream_format") or "openai")
    url = _resolve_url(conf)

    if fmt == "anthropic":
        body = {"model": model, "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]}
        headers = {"content-type": "application/json", "x-api-key": key,
                   "anthropic-version": "2023-06-01"}
    else:
        body = {"model": model, "max_tokens": 1, "stream": False,
                "messages": [{"role": "user", "content": "hi"}]}
        headers = {"content-type": "application/json", "authorization": "Bearer " + key}

    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers=headers, method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(2048).decode("utf-8", "replace")
            cost = (time.time() - t0) * 1000
            return True, _t("✅ 连通（HTTP %d，%.0f ms）" % (resp.status, cost),
                            "✅ reachable (HTTP %d, %.0f ms)" % (resp.status, cost))
    except urllib.error.HTTPError as e:
        cost = (time.time() - t0) * 1000
        detail = ""
        try:
            detail = e.read(400).decode("utf-8", "replace").replace("\n", " ")
        except Exception:
            pass
        hint = ""
        if e.code in (401, 403):
            hint = _t("（密钥无效或无权访问该模型）", "(invalid key or no access to this model)")
        elif e.code == 404:
            hint = _t("（URL 或模型名不存在）", "(URL or model not found)")
        elif e.code == 429:
            hint = _t("（限流/额度不足）", "(rate limited / quota)")
        return False, _t("❌ HTTP %d，%.0f ms %s %s" % (e.code, cost, hint, detail[:200]),
                         "❌ HTTP %d, %.0f ms %s %s" % (e.code, cost, hint, detail[:200]))
    except Exception as e:
        return False, _t("❌ 连接失败：%r（检查网络 / URL）" % (e,),
                         "❌ connection failed: %r (check network / URL)" % (e,))


def cmd_test(args: List[str]) -> int:
    _out(_t("🧪 测试连通性（max_tokens=1）…", "🧪 testing connectivity (max_tokens=1)…"))
    ok, msg = test_connection()
    _out("   " + msg)
    return 0 if ok else 1


# ══════════════════════════════════════════════════════════════════
# backup / restore
# ══════════════════════════════════════════════════════════════════

def cmd_backup(args: List[str]) -> int:
    name = args[0] if args else time.strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(BACKUP_ROOT, name)
    if os.path.exists(dest):
        _out(_t("❌ 备份已存在：%s" % dest, "❌ backup exists: %s" % dest))
        return 1
    os.makedirs(dest, mode=0o700, exist_ok=True)
    try:
        os.chmod(dest, 0o700)
    except Exception:
        pass
    manifest = {"created": time.strftime("%Y-%m-%d %H:%M:%S"), "files": []}
    for label, path in config_paths():
        if not os.path.isfile(path):
            continue
        rel = path.lstrip(os.sep).replace(os.sep, "__")
        target = os.path.join(dest, rel)
        try:
            shutil.copy2(path, target)
            os.chmod(target, 0o600)
            manifest["files"].append({"label": label, "src": path, "name": rel,
                                      "mode": _mode_of(path), "size": _size_of(path)})
        except Exception as e:
            _out(_t("  ⚠️ 跳过 %s：%r" % (path, e), "  ⚠️ skipped %s: %r" % (path, e)))
    with open(os.path.join(dest, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    os.chmod(os.path.join(dest, "manifest.json"), 0o600)
    _out(_t("✅ 已备份 %d 个文件 → %s" % (len(manifest["files"]), dest),
            "✅ backed up %d file(s) → %s" % (len(manifest["files"]), dest)))
    return 0


def list_backups() -> List[str]:
    try:
        return sorted(d for d in os.listdir(BACKUP_ROOT)
                      if os.path.isdir(os.path.join(BACKUP_ROOT, d)))
    except Exception:
        return []


def cmd_restore(args: List[str]) -> int:
    if not args:
        items = list_backups()
        if not items:
            _out(_t("（暂无备份）", "(no backups)"))
            return 1
        _out(_t("可用备份：", "available backups:"))
        for it in items[-10:]:
            _out("  " + it)
        _out(_t("用法：ai config restore <备份名>", "Usage: ai config restore <name>"))
        return 1
    name = args[0]
    src = os.path.join(BACKUP_ROOT, name)
    mf = os.path.join(src, "manifest.json")
    if not os.path.isfile(mf):
        _out(_t("❌ 备份不存在：%s" % src, "❌ backup not found: %s" % src))
        return 1
    manifest = json.load(open(mf, encoding="utf-8"))
    try:
        from . import ui
        ok = ui.confirm(_t("恢复备份 %s（覆盖当前配置，先自动做一次 pre-restore 备份）？" % name,
                           "Restore %s (overwrites current config; a pre-restore backup is made)?" % name))
    except Exception:
        ok = False
    if not ok:
        _out(_t("⛔ 已取消", "⛔ cancelled"))
        return 1
    cmd_backup(["pre-restore-" + time.strftime("%Y%m%d-%H%M%S")])
    n = 0
    for item in manifest.get("files", []):
        target = item.get("src") or ""
        path = os.path.join(src, item.get("name") or "")
        if not target or not os.path.isfile(path):
            continue
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(path, target)
            os.chmod(target, 0o600)
            n += 1
        except Exception as e:
            _out(_t("  ⚠️ 恢复失败 %s：%r" % (target, e), "  ⚠️ restore failed %s: %r" % (target, e)))
    _out(_t("✅ 已恢复 %d 个文件（重启 ai 生效）" % n,
            "✅ restored %d file(s) (restart ai to apply)" % n))
    return 0


# ══════════════════════════════════════════════════════════════════
# doctor
# ══════════════════════════════════════════════════════════════════

_PLAIN_KEY_RE = None


def _looks_plain_key(text: str) -> bool:
    """旧版 key.conf 可能存明文：形如 api_key = sk-xxxx（≥20 位连续可见字符）。"""
    global _PLAIN_KEY_RE
    if _PLAIN_KEY_RE is None:
        import re
        _PLAIN_KEY_RE = re.compile(r"(?:api[_-]?key|key)\s*[=:]\s*[\"']?([A-Za-z0-9_\-]{20,})")
    return bool(_PLAIN_KEY_RE.search(text or ""))


def cmd_doctor(args: List[str]) -> int:
    problems = []
    _out(_t("🩺 私人配置体检", "🩺 Personal config check"))
    _out("  " + "─" * 52)
    for label, path in config_paths():
        if not os.path.exists(path):
            _out("  ·  %-24s %s  %s" % (label, _t("未创建", "absent"), path))
            continue
        mode = _mode_of(path)
        sensitive = "key" in os.path.basename(path) or "index" in os.path.basename(path) \
            or "senses" in path
        flag = ""
        if sensitive and mode not in ("0o600", "0o700"):
            flag = "  ⚠️ " + _t("权限过宽，建议 chmod 600", "too permissive, chmod 600")
            problems.append("%s (%s)" % (path, mode))
        _out("  ✅ %-24s %-6s %-8s %s%s" % (label, mode, _size_of(path), path, flag))

    # 明文残留
    for cand in (_cfg.KEY_CONF_PATH + ".bak", _cfg.KEY_CONF_LEGACY_PATH,
                 _cfg.KEY_CONF_LEGACY_PATH + ".bak"):
        if not os.path.isfile(cand):
            continue
        try:
            txt = open(cand, encoding="utf-8", errors="replace").read(4096)
        except Exception:
            continue
        if _looks_plain_key(txt):
            _out(_t("  ⚠️ 疑似明文密钥残留：%s（建议删除或 chmod 600）" % cand,
                    "  ⚠️ possible plaintext key left in %s (delete or chmod 600)" % cand))
            problems.append(cand)

    # 逻辑检查
    s = _summary()
    if not s["key_ok"]:
        _out(_t("  ❌ 未配置 API Key", "  ❌ no API key configured"))
        problems.append("no-api-key")
    else:
        _out(_t("  ✅ API Key 已配置：%s" % s["key"], "  ✅ API key: %s" % s["key"]))
    if s["api_url"] and not s["api_url"].startswith("https://"):
        _out(_t("  ⚠️ API URL 不是 https：%s" % s["api_url"], "  ⚠️ API URL is not https: %s" % s["api_url"]))
        problems.append("insecure-url")
    plat = s["platform"]
    if plat and plat != "custom":
        info = _cfg._SUPPORTED_PLATFORMS.get(plat) or {}
        if s["model"] and info.get("models") and s["model"] not in info["models"]:
            _out(_t("  ⚠️ 模型 `%s` 不在 %s 的模型表里（可能是自定义/已下线）"
                    % (s["model"], plat),
                    "  ⚠️ model `%s` not in %s's model list (custom or retired)"
                    % (s["model"], plat)))
            problems.append("unknown-model")
        if not s["model"]:
            _out(_t("  ⚠️ 未设置模型（将用平台默认 %s）" % info.get("default_model"),
                    "  ⚠️ no model set (default %s will be used)" % info.get("default_model")))
    if list_backups():
        _out(_t("  ✅ 已有备份：%d 个（%s）" % (len(list_backups()), BACKUP_ROOT),
                "  ✅ backups: %d (%s)" % (len(list_backups()), BACKUP_ROOT)))
    else:
        _out(_t("  ·  还没有备份：`ai config backup` 建一个",
                "  ·  no backup yet: run `ai config backup`"))

    _out("")
    if problems:
        if any("0o" in str(p) for p in problems):
            _out(_t("  提示：Android 外置存储/FUSE 上 chmod 600 可能不生效（显示 0o660 属正常），"
                    "内部存储或 Termux 私有目录才会真正收紧。",
                    "  Note: on Android external storage/FUSE chmod 600 may not apply "
                    "(0o660 is expected); only app-private/Termux dirs really tighten."))
        _out(_t("  发现 %d 个可改进项（见上面 ⚠️/❌）" % len(problems),
                "  %d item(s) to improve (see ⚠️/❌ above)" % len(problems)))
        return 1
    _out(_t("  ✅ 配置健康", "  ✅ config looks healthy"))
    return 0


# ══════════════════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════════════════

def run(sub: str = "show", extra: Optional[List[str]] = None) -> int:
    extra = list(extra or [])
    act = (sub or "show").strip().lower()
    if act in ("", "show"):
        return cmd_show(extra)
    if act == "get":
        return cmd_get(extra)
    if act == "set":
        return cmd_set(extra)
    if act == "test":
        return cmd_test(extra)
    if act == "backup":
        return cmd_backup(extra)
    if act == "restore":
        return cmd_restore(extra)
    if act == "doctor":
        return cmd_doctor(extra)
    if act == "path":
        return cmd_path(extra)
    _out(_t("用法：ai config <%s>" % "|".join(ACTIONS),
            "Usage: ai config <%s>" % "|".join(ACTIONS)))
    return 1
