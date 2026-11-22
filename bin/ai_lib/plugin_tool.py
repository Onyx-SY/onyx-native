# -*- coding: utf-8 -*-
"""AI 侧插件管理（plugin 工具）——让 AI 在用户明确授权下自助注册 C 插件。

用户只需说：「把 /path/libfoo.so 注册进来，授权文件是 /path/foo.key」，
AI 调本工具的 add 动作：先弹窗给用户看**绝对路径 + 大小 + sha256 + 授权文件**，
用户同意才真正写入注册索引（~/.ai_s/plugin_tool/index.json）。

安全边界：
- 只接受 .so/.dll/.dylib 真实文件，路径必须存在
- 每次 add/remove 都必须用户确认（加载本机原生代码属于危险操作）
- 确认被拒 → 不写索引，返回说明
- 不读密钥内容，只记录用户指定的路径（与 `ai plugin-add` 行为一致）
"""
import contextlib
import hashlib
import io
import json
import os
from typing import Any, Dict, List

from . import ui

ACTIONS = ("list", "info", "verify", "schema", "add", "remove", "refresh", "daemon")
_LIB_EXT = (".so", ".dll", ".dylib")


def _lang() -> str:
    try:
        from .config import get_current_lang
        return get_current_lang()
    except Exception:
        return "chinese"


def _t(cn: str, en: str) -> str:
    return cn if str(_lang()).lower().startswith(("chi", "zh")) else en


def _loader():
    """延迟导入 plugin_loader（可能在 bin/ 或包内两处布局）。"""
    try:
        from .. import plugin_loader  # type: ignore
        return plugin_loader
    except Exception:
        pass
    try:
        from bin import plugin_loader  # type: ignore
        return plugin_loader
    except Exception:
        import plugin_loader  # type: ignore
        return plugin_loader


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(262144), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0
    return str(n)


def _confirm(question: str) -> bool:
    try:
        return bool(ui.confirm(question))
    except Exception:
        # 无 UI 环境（脚本/子代理）→ 保守拒绝
        return False


def _capture(fn, *a, **kw):
    """调用 plugin_loader 里会 print 的函数，捕获输出。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ret = fn(*a, **kw)
    return ret, buf.getvalue().strip()


# ══════════════════════════════════════════════════════════════════
# 动作
# ══════════════════════════════════════════════════════════════════

def _act_list() -> str:
    pl = _loader()
    try:
        items = pl.list_plugins()
    except Exception as e:
        return _t("❌ 读取插件列表失败：%r" % (e,), "❌ failed to list plugins: %r" % (e,))
    if not items:
        return _t("（暂无已注册插件）\n提示：`ai plugin-add <库路径> key <授权文件>`，"
                  "或让我用 plugin 工具帮你注册。",
                  "(no plugins registered)\nTip: `ai plugin-add <lib> key <keyfile>`, "
                  "or ask me to register it with the plugin tool.")
    out = [_t("已注册插件 %d 个：" % len(items), "Registered plugins (%d):" % len(items))]
    for it in items:
        mark = "✅" if it.get("verified") else "⚠️"
        out.append("%s %-18s %-8s %s" % (mark, it.get("name"), _human(int(it.get("size") or 0)),
                                         it.get("status")))
        out.append("    lib: %s" % it.get("path"))
        if it.get("key"):
            out.append("    key: %s" % it.get("key"))
        if it.get("schema"):
            nm = (it["schema"].get("name") or it["schema"].get("function", {}).get("name") or "")
            if nm:
                out.append(_t("    AI 工具名: %s" % nm, "    AI tool name: %s" % nm))
    return "\n".join(out)


def _act_info(name: str) -> str:
    if not name:
        return _t("❌ 需要 name", "❌ name required")
    pl = _loader()
    entry = pl._index_entry(name)
    if not entry:
        return _t("❌ 未注册：%s" % name, "❌ not registered: %s" % name)
    lib = entry.get("lib") or ""
    ok, reason, payload = pl.verify(name)
    lines = [
        _t("插件：%s" % name, "Plugin: %s" % name),
        "  lib: %s" % lib,
        "  src: %s" % (entry.get("src") or "-"),
        "  key: %s" % (entry.get("key") or _t("无", "none")),
        "  added: %s   system: %s" % (entry.get("added"), entry.get("system")),
        "  size: %s" % (_human(os.path.getsize(lib)) if lib and os.path.isfile(lib) else "-"),
        "  sha256: %s" % (_sha256(lib) if lib and os.path.isfile(lib) else "-"),
        "  %s: %s" % (_t("授权", "license"), "✅ %s" % reason if ok else "⚠️ %s" % reason),
    ]
    sch = entry.get("schema")
    if isinstance(sch, dict):
        fn = sch.get("function") if isinstance(sch.get("function"), dict) else sch
        lines.append("  %s: %s" % (_t("AI 工具", "AI tool"), fn.get("name") or "-"))
        if fn.get("description"):
            lines.append("    %s" % str(fn["description"])[:200])
    return "\n".join(lines)


def _act_verify(name: str) -> str:
    if not name:
        return _t("❌ 需要 name", "❌ name required")
    pl = _loader()
    ok, reason, payload = pl.verify(name)
    head = "✅ " if ok else "⚠️ "
    return head + _t("校验结果：%s" % reason, "verify: %s" % reason)


def _act_schema(name: str) -> str:
    if not name:
        return _t("❌ 需要 name", "❌ name required")
    pl = _loader()
    sch = pl.tool_schema(name)
    if not sch:
        return _t("⚠️ 该插件没有导出 plugin_tool_schema()（无法作为 AI 工具调用）",
                  "⚠️ plugin exports no plugin_tool_schema() (not callable as an AI tool)")
    return json.dumps(sch, ensure_ascii=False, indent=2)[:4000]


def _act_add(lib: str, key: str) -> str:
    if not lib:
        return _t("❌ 需要 lib（动态链接库绝对路径）", "❌ lib path required")
    path = os.path.realpath(os.path.abspath(os.path.expanduser(lib)))
    if not os.path.isfile(path):
        return _t("❌ 动态链接库不存在：%s" % path, "❌ shared library not found: %s" % path)
    if not path.lower().endswith(_LIB_EXT):
        return _t("❌ 不是动态链接库（需 .so/.dll/.dylib）：%s" % path,
                  "❌ not a shared library (.so/.dll/.dylib): %s" % path)
    keyp = ""
    if key:
        keyp = os.path.realpath(os.path.abspath(os.path.expanduser(key)))
        if not os.path.isfile(keyp):
            return _t("❌ 授权文件不存在：%s" % keyp, "❌ key file not found: %s" % keyp)

    size = os.path.getsize(path)
    digest = _sha256(path)
    msg = _t(
        "注册插件会加载本机原生代码（.so），请确认来源可信：\n\n"
        "  库文件：%s\n  大小：%s\n  sha256：%s\n  授权文件：%s\n\n是否注册？"
        % (path, _human(size), digest[:32] + "…", keyp or "（无）"),
        "Registering a plugin loads native code (.so). Confirm the source is trusted:\n\n"
        "  lib: %s\n  size: %s\n  sha256: %s\n  key: %s\n\nRegister now?"
        % (path, _human(size), digest[:32] + "…", keyp or "(none)"))
    if not _confirm(msg):
        return _t("⛔ 用户取消，未注册（索引未改动）", "⛔ cancelled by user; index unchanged")

    pl = _loader()
    ok, out = _capture(pl.plugin_add, path, keyp or None)
    if not ok:
        return _t("❌ 注册失败：%s" % (out or "plugin_add returned False"),
                  "❌ registration failed: %s" % (out or "plugin_add returned False"))

    name = pl._modname(path)
    vok, vreason, _payload = pl.verify(name)
    try:
        pl.refresh_tool_schemas()
    except Exception:
        pass
    try:
        from .native_tools import invalidate_native_tools_cache
        invalidate_native_tools_cache()
    except Exception:
        pass

    lines = [
        _t("✅ 已注册插件 [%s]" % name, "✅ plugin registered: [%s]" % name),
        "  lib: %s" % path,
        "  key: %s" % (keyp or _t("无", "none")),
        "  %s: %s" % (_t("授权", "license"), "✅ %s" % vreason if vok else "⚠️ %s" % vreason),
    ]
    sch = None
    try:
        sch = pl.tool_schema(name)
    except Exception:
        sch = None
    if sch:
        fn = sch.get("function") if isinstance(sch.get("function"), dict) else sch
        lines.append(_t("  AI 工具名：%s（本轮即可调用）" % (fn.get("name") or name),
                        "  AI tool: %s (callable this turn)" % (fn.get("name") or name)))
    else:
        lines.append(_t("  ⚠️ 该插件未导出 plugin_tool_schema()，无法作为 AI 工具调用",
                        "  ⚠️ no plugin_tool_schema(); not callable as an AI tool"))
    if out:
        lines.append("  " + out.replace("\n", "\n  "))
    return "\n".join(lines)


def _act_remove(name: str) -> str:
    if not name:
        return _t("❌ 需要 name", "❌ name required")
    pl = _loader()
    entry = pl._index_entry(name)
    if not entry:
        return _t("❌ 未注册：%s" % name, "❌ not registered: %s" % name)
    msg = _t("确认从注册索引中移除插件 [%s]？\n  库文件不会被删除：%s"
             % (name, entry.get("lib") or "-"),
             "Remove plugin [%s] from the registry?\n  the library file is NOT deleted: %s"
             % (name, entry.get("lib") or "-"))
    if not _confirm(msg):
        return _t("⛔ 用户取消，索引未改动", "⛔ cancelled; index unchanged")
    ok, out = _capture(pl.plugin_remove, name)
    try:
        from .native_tools import invalidate_native_tools_cache
        invalidate_native_tools_cache()
    except Exception:
        pass
    return (_t("✅ 已移除 [%s]" % name, "✅ removed [%s]" % name) if ok
            else _t("❌ 移除失败：%s" % out, "❌ remove failed: %s" % out))


def _act_refresh() -> str:
    pl = _loader()
    try:
        n = pl.refresh_tool_schemas()
    except Exception as e:
        return _t("❌ 刷新失败：%r" % (e,), "❌ refresh failed: %r" % (e,))
    try:
        from .native_tools import invalidate_native_tools_cache
        invalidate_native_tools_cache()
    except Exception:
        pass
    return _t("✅ 已刷新 %d 个插件的工具说明" % n, "✅ refreshed tool schemas for %d plugin(s)" % n)


def _act_daemon(name: str, daemon_action: str) -> str:
    pl = _loader()
    act = (daemon_action or "status").strip().lower()
    try:
        if act in ("start", "stop", "status"):
            if not name:
                return _t("❌ daemon 需要 name", "❌ daemon requires name")
            fn = {"start": pl.daemon_start, "stop": pl.daemon_stop,
                  "status": pl.daemon_status}[act]
            res = fn(name)
            return "%s: %s" % (act, json.dumps(res, ensure_ascii=False)[:1500])
        res = pl.ensure_plugin_daemons()
        return json.dumps(res, ensure_ascii=False)[:2000]
    except Exception as e:
        return _t("❌ daemon 操作失败：%r" % (e,), "❌ daemon error: %r" % (e,))


# ══════════════════════════════════════════════════════════════════
# 对外入口
# ══════════════════════════════════════════════════════════════════

def watchable_tools() -> List[str]:
    """可作为感官注意力目标的插件工具名（供 senses 展示）。"""
    try:
        pl = _loader()
        return sorted(pl.plugin_tools_schemas().keys())
    except Exception:
        return []


def dispatch(action: str = "list", lib: str = "", key: str = "", name: str = "",
             daemon_action: str = "", **kw: Any) -> str:
    act = (action or "list").strip().lower()
    if act not in ACTIONS:
        return _t("❌ 未知 action：%s（可用：%s）" % (act, ", ".join(ACTIONS)),
                  "❌ unknown action: %s (available: %s)" % (act, ", ".join(ACTIONS)))
    if act == "list":
        return _act_list()
    if act == "info":
        return _act_info(name)
    if act == "verify":
        return _act_verify(name)
    if act == "schema":
        return _act_schema(name)
    if act == "add":
        return _act_add(lib, key)
    if act == "remove":
        return _act_remove(name)
    if act == "refresh":
        return _act_refresh()
    return _act_daemon(name, daemon_action)
