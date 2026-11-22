# -*- coding: utf-8 -*-
"""补丁：为 senses.py 增加「采样颗粒度 / 定时执行工具 / 静默提醒」支持。"""
import io
import sys

P = "bin/ai_lib/senses.py"
s = io.open(P, encoding="utf-8").read()
_n = [0]


def rep(old, new, cnt=1):
    global s
    c = s.count(old)
    if c != cnt:
        print("FAIL count=%d (expect %d): %r" % (c, cnt, old[:80]))
        sys.exit(1)
    s = s.replace(old, new, cnt)
    _n[0] += 1


# ── 1. 常量：silent 状态 + 安全工具白名单 + 自动执行器 ──
rep('''COMMAND_WINDOW_TAIL_SEC = 2.0  # RunCommand 结束后的模糊窗口''',
    '''COMMAND_WINDOW_TAIL_SEC = 2.0  # RunCommand 结束后的模糊窗口

# 定时自动执行白名单（只读、无副作用、不依赖主线程 UI）
SAFE_TOOL_WHITELIST = frozenset({
    "read_file", "get_file_info", "grep_search", "glob_search", "search_file",
    "list_directory", "directory_tree", "git_status", "git_diff", "git_log",
    "git_branch", "EnvProbe", "py_diagnostics", "py_symbols", "LspDiagnostics",
    "LspSymbols", "LspHover", "LspReferences",
})


def _run_safe_tool(tool: str, params: dict):
    """在感官线程里执行只读工具（白名单内），返回 (ok, text)。"""
    try:
        from .mcp_exec import execute_mcp_tool
        ok, out = execute_mcp_tool(tool, dict(params or {}), "filesystem", "low")
        return bool(ok), str(out or "")[:4000]
    except Exception as e:
        return False, "error: %r" % (e,)''')

rep('''    "focus_missing": "NO-KEYWORD",''',
    '''    "focus_missing": "NO-KEYWORD",
    "silent": "SILENT",''')

# ── 2. _tick：静默提醒 ──
rep('''            elif a.get("type") == "tool":
                self._check_tool_interval(a, now, cfg)''',
    '''            elif a.get("type") == "tool":
                self._check_tool_interval(a, now, cfg)
            self._check_silent(a, now)''')

# ── 3. _check_silent + _check_tool_interval 重写 ──
rep('''    def _check_tool_interval(self, a: Dict[str, Any], now: float, cfg: Dict[str, Any]) -> None:
        if (a.get("trigger") or "") != "interval":
            return
        iv = float(a.get("interval_sec") or 0)
        if iv <= 0:
            return
        last = float(a.get("_last_fire") or 0)
        if now - last >= iv:
            a["_last_fire"] = now
            self._push_event(a, {"state": "ok", "text": self._tool_last.get(a.get("tool") or "", {}).get("text", ""),
                                 "prev": "", "changed_at": now})''',
    '''    def _check_silent(self, a: Dict[str, Any], now: float) -> None:
        """长时间没有任何变化 → 主动提醒一次（颗粒度兜底，每次静默只提醒一次）。"""
        silent = float(a.get("silent_sec") or 0)
        if silent <= 0:
            return
        aid = int(a.get("id") or 0)
        snap = self._snap.get(aid) or {}
        base = float(snap.get("changed_at") or a.get("created") or 0)
        if not base or now - base < silent:
            return
        if float(a.get("_silent_fired") or 0) >= base:
            return
        a["_silent_fired"] = now
        self._push_event(a, {"state": "silent", "text": snap.get("text") or "",
                             "prev": "", "changed_at": now})

    def _check_tool_interval(self, a: Dict[str, Any], now: float, cfg: Dict[str, Any]) -> None:
        """定时执行工具 / 定时提醒：间隔由 interval_sec 控制（秒）。"""
        if (a.get("trigger") or "") != "interval":
            return
        iv = float(a.get("interval_sec") or 0)
        if iv <= 0:
            return
        last = float(a.get("_last_fire") or 0)
        if now - last < iv:
            return
        a["_last_fire"] = now
        tool = a.get("tool") or ""
        if a.get("exec_tool") and tool in SAFE_TOOL_WHITELIST:
            ok, out = _run_safe_tool(tool, a.get("tool_args") or {})
            text = "[auto-run %s] %s\\n%s" % (tool, "ok" if ok else "fail", out or "")
        else:
            text = (self._tool_last.get(tool, {}).get("text", "") or
                    "[interval] %s 未自动执行（非只读白名单），需 AI 亲自运行" % tool)
        aid = int(a.get("id") or 0)
        prev = self._tool_last.get(tool) or {}
        self._tool_last[tool] = {"text": text, "ts": now}
        self._snap[aid] = {"sha": _sha(text), "text": text, "prev": prev.get("text", ""),
                           "state": "external", "changed_at": now,
                           "line_no": 0, "truncated": False}
        self._dirty = True
        if a.get("notify", True):
            self._push_event(a, self._snap[aid])''')

# ── 4. add_file：采样间隔 + 静默提醒 ──
rep('''def add_file(path: str, start: int = 1, end: int = 200, keyword: str = "",
             context: int = 0, unit: str = "lines", trigger: str = "on_change",
             debounce_sec: Optional[float] = None, note: str = "") -> Dict[str, Any]:''',
    '''def add_file(path: str, start: int = 1, end: int = 200, keyword: str = "",
             context: int = 0, unit: str = "lines", trigger: str = "on_change",
             debounce_sec: Optional[float] = None, note: str = "",
             check_interval_sec: float = 0, silent_sec: float = 0) -> Dict[str, Any]:''')

rep('''        "trigger": trig,
        "debounce_sec": float(debounce_sec) if debounce_sec else 0.0,
        "interval_sec": 0,
        "enabled": True,''',
    '''        "trigger": trig,
        "debounce_sec": float(debounce_sec) if debounce_sec else 0.0,
        "check_interval_sec": float(check_interval_sec or 0),
        "silent_sec": float(silent_sec or 0),
        "interval_sec": 0,
        "enabled": True,''')

# ── 5. add_tool：定时执行 ──
rep('''def add_tool(tool: str, match: str = "", trigger: str = "on_tool",
             interval_sec: float = 0, keyword: str = "", context: int = 120,
             unit: str = "bytes", note: str = "") -> Dict[str, Any]:''',
    '''def add_tool(tool: str, match: str = "", trigger: str = "on_tool",
             interval_sec: float = 0, keyword: str = "", context: int = 120,
             unit: str = "bytes", note: str = "", exec_tool: bool = False,
             tool_args: Optional[Dict[str, Any]] = None, notify: bool = True,
             silent_sec: float = 0) -> Dict[str, Any]:''')

rep('''        "trigger": trig,
        "interval_sec": float(interval_sec or 0),
        "debounce_sec": 0.0,
        "enabled": True,''',
    '''        "trigger": trig,
        "interval_sec": float(interval_sec or 0),
        "exec_tool": bool(exec_tool),
        "tool_args": dict(tool_args or {}),
        "notify": bool(notify),
        "silent_sec": float(silent_sec or 0),
        "debounce_sec": 0.0,
        "enabled": True,''')

# ── 6. update_attention 可改字段 ──
rep('''        for k in ("start", "end", "trigger", "debounce_sec", "interval_sec",
                  "match", "enabled", "note"):''',
    '''        for k in ("start", "end", "trigger", "debounce_sec", "interval_sec",
                  "match", "enabled", "note", "check_interval_sec", "silent_sec",
                  "exec_tool", "tool_args", "notify"):''')

# ── 7. _fmt_attention：展示颗粒度 ──
rep('''def _fmt_attention(a: Dict[str, Any]) -> str:
    aid = int(a.get("id") or 0)
    if a.get("type") == "tool":
        return "#%d TOOL %s | trigger=%s%s | %s" % (
            aid, a.get("tool") or "", a.get("trigger") or "on_tool",
            (" match=%r" % a.get("match")) if a.get("match") else "",
            a.get("note") or "")
    fd = _focus_desc(a)
    rng = ("@L%d %s" % (int(a.get("line_no") or 0), fd)) if fd else \\
        "L%d-L%d" % (int(a.get("start") or 1), int(a.get("end") or 0))
    return "#%d FILE %s : %s | trigger=%s%s | STATE=%s | %s | %s" % (
        aid, a.get("path") or "", rng, a.get("trigger") or "on_change",
        (" debounce=%.0fs" % float(a.get("debounce_sec") or 0)) if a.get("debounce_sec") else "",
        a.get("state") or "OK", a.get("changed_ago") or "-", a.get("note") or "")''',
    '''def _fmt_attention(a: Dict[str, Any]) -> str:
    aid = int(a.get("id") or 0)
    extra = ""
    if float(a.get("interval_sec") or 0):
        extra += " every=%.0fs" % float(a.get("interval_sec"))
        if a.get("exec_tool"):
            extra += "(auto-exec)"
    if float(a.get("check_interval_sec") or 0):
        extra += " poll=%.0fs" % float(a.get("check_interval_sec"))
    if float(a.get("silent_sec") or 0):
        extra += " silent=%.0fs" % float(a.get("silent_sec"))
    if a.get("type") == "tool":
        return "#%d TOOL %s | trigger=%s%s%s | %s" % (
            aid, a.get("tool") or "", a.get("trigger") or "on_tool",
            (" match=%r" % a.get("match")) if a.get("match") else "", extra,
            a.get("note") or "")
    fd = _focus_desc(a)
    rng = ("@L%d %s" % (int(a.get("line_no") or 0), fd)) if fd else \\
        "L%d-L%d" % (int(a.get("start") or 1), int(a.get("end") or 0))
    return "#%d FILE %s : %s | trigger=%s%s%s | STATE=%s | %s | %s" % (
        aid, a.get("path") or "", rng, a.get("trigger") or "on_change",
        (" debounce=%.0fs" % float(a.get("debounce_sec") or 0)) if a.get("debounce_sec") else "",
        extra, a.get("state") or "OK", a.get("changed_ago") or "-", a.get("note") or "")''')

# ── 8. handle_eyes_action：透传新参数 ──
rep('''                trigger=str(p.get("trigger") or "on_change"),
                debounce_sec=p.get("debounce_sec"),
                note=str(p.get("note") or ""),
            )''',
    '''                trigger=str(p.get("trigger") or "on_change"),
                debounce_sec=p.get("debounce_sec"),
                note=str(p.get("note") or ""),
                check_interval_sec=p.get("check_interval_sec") or 0,
                silent_sec=p.get("silent_sec") or 0,
            )''')

rep('''                unit=str(p.get("unit") or "bytes"),
                note=str(p.get("note") or ""),
            )''',
    '''                unit=str(p.get("unit") or "bytes"),
                note=str(p.get("note") or ""),
                exec_tool=bool(p.get("exec_tool")),
                tool_args=p.get("tool_args") or {},
                notify=bool(p.get("notify", True)),
                silent_sec=p.get("silent_sec") or 0,
            )''')

rep('''            for k in ("start", "end", "trigger", "debounce_sec", "interval_sec",
                      "match", "enabled", "note"):''',
    '''            for k in ("start", "end", "trigger", "debounce_sec", "interval_sec",
                      "match", "enabled", "note", "check_interval_sec", "silent_sec",
                      "exec_tool", "notify", "tool_args"):''')

io.open(P, "w", encoding="utf-8").write(s)
print("OK patches=%d" % _n[0])
