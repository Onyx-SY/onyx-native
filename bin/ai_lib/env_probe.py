# -*- coding: utf-8 -*-
"""
env_probe.py — EnvProbe 环境探测工具（只读，秒回）

从 bin/ai_cmd.py 拆分（模块化架构重构）：
- 标准库依赖 + 局部 import，无 ai_cmd 闭包依赖；
- _ENV_PROBE_TYPES 配置驱动：type 决定探测范围（sections）+ 工具子集 + 专属探测。
"""

import os
import re
import sys
from typing import List, Optional


_ENV_PROBE_TOOLS = [
    "python3", "python", "pip", "pip3", "git", "curl", "wget",
    "nmap", "netstat", "ss", "ping", "ifconfig", "ip", "arp", "lsof", "fuser",
    "tar", "unzip", "gzip", "gcc", "make", "node", "npm", "npx", "java", "go",
    "docker", "kubectl", "sqlite3", "redis-cli", "mysql", "psql", "dig",
    "nslookup", "host", "openssl", "base64", "xxd", "od", "hexdump", "jq", "nc",
    "socat", "tshark", "tcpdump", "msfconsole", "hydra", "sqlmap", "nikto",
    "gobuster", "ffuf", "john", "hashcat", "busybox", "toybox", "termux-info",
    "bash", "zsh", "fish", "sh",
]

# ── EnvProbe 任务类型：type 参数决定探测范围（sections）+ 工具子集 + 专属探测 ──
# sections 可选块：system / user / network / disk / tools；tools=None 表示全量清单；
# extra 为 (标签, 命令) 列表，命令失败静默跳过。
_ENV_PROBE_TYPES = {
    "general": {
        "sections": ["system", "user", "network", "disk", "tools"],
        "tools": None,
        "extra": [],
    },
    "deploy": {
        "sections": ["system", "user", "network", "disk", "tools"],
        "tools": ["python3", "pip", "git", "curl", "wget", "tar", "unzip", "gzip",
                  "docker", "kubectl", "sqlite3", "openssl", "bash", "node", "npm",
                  "go", "gcc", "make", "systemctl"],
        "extra": [("内存", "free -h 2>/dev/null | head -3"),
                  ("CPU 核数", "nproc 2>/dev/null")],
    },
    "network": {
        "sections": ["system", "user", "network", "tools"],
        "tools": ["curl", "wget", "nmap", "zenmap", "masscan", "netstat", "ss",
                  "ping", "ifconfig", "ip", "arp", "arp-scan", "netdiscover",
                  "lsof", "fuser", "ncat", "nc", "socat", "dig", "nslookup", "host",
                  "dnsenum", "dnsrecon", "fierce", "dnsmap", "theHarvester",
                  "subfinder", "amass", "nuclei", "tshark", "tcpdump", "wireshark",
                  "ettercap", "bettercap", "responder", "hydra", "medusa", "ncrack",
                  "patator", "snmpwalk", "onesixtyone", "nbtscan", "enum4linux",
                  "smbmap", "smbclient", "aircrack-ng", "airodump-ng", "aireplay-ng",
                  "reaver", "crunch", "wifite", "macchanger", "proxychains", "msfconsole"],
        "extra": [("监听端口", "ss -tln 2>/dev/null | head -10 || netstat -tln 2>/dev/null | head -10"),
                  ("无线接口", "iwconfig 2>/dev/null | head -6")],
    },
    "python": {
        "sections": ["system", "user", "tools"],
        "tools": ["python3", "python", "pip", "pip3", "uv", "poetry", "conda",
                  "pytest", "flake8", "mypy", "ruff"],
        "extra": [("pip", "python3 -m pip --version 2>/dev/null | head -1"),
                  ("关键包", "python3 -c \"import importlib.util as _i; print([m for m in ('flask','django','requests','rich','bs4','lxml','numpy','pandas') if _i.find_spec(m)] or '无')\" 2>/dev/null")],
    },
    "build": {
        "sections": ["system", "user", "disk", "tools"],
        "tools": ["gcc", "g++", "clang", "make", "cmake", "ninja", "go", "rustc",
                  "cargo", "node", "npm", "npx", "java", "ld", "meson", "pkg-config"],
        "extra": [("gcc", "gcc --version 2>/dev/null | head -1"),
                  ("go", "go version 2>/dev/null"),
                  ("node", "node --version 2>/dev/null"),
                  ("rustc", "rustc --version 2>/dev/null")],
    },
    "database": {
        "sections": ["system", "tools"],
        "tools": ["sqlite3", "mysql", "mysqld", "psql", "redis-cli", "mongod",
                  "mongo", "mongosh", "clickhouse-client", "duckdb"],
        "extra": [("sqlite3", "sqlite3 --version 2>/dev/null | head -1"),
                  ("mysql", "mysql --version 2>/dev/null"),
                  ("psql", "psql --version 2>/dev/null"),
                  ("redis", "redis-cli --version 2>/dev/null")],
    },
    "web": {
        "sections": ["system", "network", "tools"],
        "tools": ["node", "npm", "npx", "pnpm", "yarn", "bun", "curl", "wget",
                  "nginx", "apache2", "httpd", "php", "openssl", "sqlmap", "nikto",
                  "gobuster", "ffuf", "dirb", "dirsearch", "feroxbuster", "wpscan",
                  "whatweb", "wafw00f", "xsstrike", "commix", "dalfox", "arjun",
                  "paramspider", "jwt_tool", "nuclei", "httpx", "subfinder", "amass",
                  "katana", "gau", "burpsuite", "zaproxy", "beef-xss", "msfvenom",
                  "searchsploit", "msfconsole"],
        "extra": [("node", "node --version 2>/dev/null"),
                  ("npm", "npm --version 2>/dev/null"),
                  ("nginx", "nginx -v 2>&1 | head -1"),
                  ("php", "php --version 2>/dev/null | head -1"),
                  ("本地 Web 端口", "ss -tln 2>/dev/null | grep -E ':(80|443|8000|8080|3000|5000|8888|9000) ' | head -8 || netstat -tln 2>/dev/null | grep -E ':(80|443|8000|8080|3000|5000|8888|9000) ' | head -8")],
    },
    "permission": {
        "sections": ["system", "user", "tools"],
        "tools": ["sudo", "su", "doas", "chmod", "chown", "setfacl", "getfacl",
                  "openssl", "ssh", "gpg"],
        "extra": [("完整身份", "id 2>/dev/null"),
                  ("SELinux", "getenforce 2>/dev/null")],
    },
}


def _env_probe_run(cmd: str, timeout: int = 3) -> str:
    """EnvProbe 内部探测：subprocess 快速执行，失败静默。"""
    import subprocess as _sp
    try:
        _r = _sp.run(cmd, shell=True, capture_output=True, text=True,
                     errors="replace", timeout=timeout)
        return ((_r.stdout or "").strip() + "\n" + (_r.stderr or "").strip()).strip()
    except Exception:
        return ""


def _env_section_system() -> List[str]:
    import platform as _pf
    lines = ["### 系统", f"- OS: {_pf.system()} {_pf.release()}"]
    _ver = _pf.version() or ""
    if _ver:
        lines.append(f"- 版本: {_ver[:80]}")
    lines.append(f"- 架构: {_pf.machine()}")
    lines.append(f"- Python: {_pf.python_version()}")
    lines.append(f"- 解释器: {sys.executable}")
    _uname = _env_probe_run("uname -a")
    if _uname:
        lines.append(f"- uname: {_uname[:140]}")
    return lines


def _env_section_user() -> List[str]:
    import getpass as _gp
    lines = ["### 用户与权限"]
    try:
        lines.append(f"- 用户: {_gp.getuser()}")
    except Exception:
        pass
    _is_root = hasattr(os, "geteuid") and os.geteuid() == 0
    lines.append(f"- 权限: {'✅ root（可执行 -O/-sU 等特权扫描）' if _is_root else '⚠️ 普通用户（非 root）：nmap -O/-sU 会直接退出、/proc/net/* 只读受限'}")
    lines.append(f"- 工作目录: {os.getcwd()}")
    lines.append(f"- 用户目录: {os.path.expanduser('~')}")
    lines.append(f"- Shell: {os.environ.get('SHELL') or os.environ.get('ComSpec') or '?'}")
    _lang = os.environ.get("LANG") or os.environ.get("LC_ALL") or ""
    lines.append(f"- locale: {_lang or '(未设置)'}")
    return lines


def _env_section_network() -> List[str]:
    lines = ["### 网络"]
    _iface = _env_probe_run("ip -o addr 2>/dev/null | grep -v ' lo ' | head -5") or \
             _env_probe_run("ifconfig 2>/dev/null | grep -E '^(eth|wlan|en|wl|br|docker|virbr)|inet ' | head -12")
    if _iface:
        lines.append(f"- 接口/地址:\n{_iface[:500]}")
    else:
        lines.append("- 接口: （无法枚举：无 ip/ifconfig 或权限受限）")
    _route = _env_probe_run("ip route 2>/dev/null | head -4") or \
             _env_probe_run("route -n 2>/dev/null | head -6")
    if _route:
        lines.append(f"- 路由:\n{_route[:300]}")
    else:
        lines.append("- 路由: （无法读取）")
    return lines


def _env_section_disk() -> List[str]:
    _df = _env_probe_run("df -h 2>/dev/null | head -6")
    if not _df:
        return []
    return ["### 磁盘", f"```\n{_df}\n```"]


def _env_section_tools(tools: Optional[List[str]] = None) -> List[str]:
    import shutil as _sh
    _list = tools if tools else _ENV_PROBE_TOOLS
    _avail, _missing = [], []
    for _t in _list:
        (_avail if _sh.which(_t) else _missing).append(_t)
    return ["### 命令可用性",
            f"- ✅ 可用 ({len(_avail)}): {', '.join(_avail)}",
            f"- ❌ 缺失 ({len(_missing)}): {', '.join(_missing)}"]


# which 参数允许的命令名字符（拒绝 shell 元字符，防注入）
_ENV_WHICH_NAME_RE = re.compile(r"^[A-Za-z0-9_\-\.\+/]+$")


def _env_probe_parse_types(probe_type: str) -> List[str]:
    """解析逗号分隔的 type 列表：去重保序；非法项忽略，全非法或空 → ['general']。"""
    _ts = []
    for _t in re.split(r"[,，\s]+", probe_type or ""):
        _t = _t.strip().lower()
        if _t in _ENV_PROBE_TYPES and _t not in _ts:
            _ts.append(_t)
    return _ts or ["general"]


def _env_probe_which_lines(which: str) -> List[str]:
    """指定命令查询：shutil.which 找路径 + 无 shell 参数列表取版本（--version/-V/-v）。"""
    import shutil as _sh
    import subprocess as _sp
    _cmds = [c for c in re.split(r"[,，\s]+", which or "") if c.strip()]
    if not _cmds:
        return []
    lines = ["### 指定命令查询"]
    for _c in _cmds[:10]:  # 上限 10 个，防滥用
        if not _ENV_WHICH_NAME_RE.fullmatch(_c):
            lines.append(f"- ⚠️ {_c[:40]}: 非法命令名（仅支持单个命令名，不能带参数）")
            continue
        _p = _sh.which(_c)
        if not _p:
            lines.append(f"- ❌ {_c}: 未找到（PATH 中不存在）")
            continue
        _ver = ""
        for _flag in ("--version", "-V", "-v"):
            try:
                _r = _sp.run([_p, _flag], capture_output=True, text=True,
                             errors="replace", timeout=2)
                _out = ((_r.stdout or "").strip() + " " + (_r.stderr or "").strip()).strip()
                if _out:
                    _ver = _out.splitlines()[0][:80]
                    break
            except Exception:
                continue
        if _ver:
            lines.append(f"- ✅ {_c}: {_p}（{_ver}）")
        else:
            lines.append(f"- ✅ {_c}: {_p}")
    return lines


def _exec_env_probe(probe_type: str = "", which: str = "") -> str:
    """EnvProbe：按 AI 指定的任务类型动态探测环境（只读，秒回）。

    - type=general（缺省）：全量报告（OS/架构/内核/Python/权限/网络/磁盘/工具表）
    - type=deploy/network/python/build/database/web/permission：只探测相关块 +
      该类型专属命令（版本/端口等），省 token
    - type 支持逗号组合多个（如 'web,network'）：sections/tools/extra 取并集
    - which=cmd1,cmd2：查询指定命令的路径与版本；仅传 which（未显式给 type）时
      输出轻量结果（系统摘要 + 查询），不跑全量
    """
    _ts = _env_probe_parse_types(probe_type)
    _explicit = bool((probe_type or "").strip())

    # 轻量模式：只查命令（未显式指定 type）
    if (which or "").strip() and not _explicit:
        _lines = ["## 📡 环境探测（轻量查询）", ""] + _env_section_system()
        _lines.append("")
        _lines += _env_probe_which_lines(which)
        return "\n".join(_lines)

    lines = ["## 📡 环境探测报告", ""]
    _secs_order = ["system", "user", "network", "disk", "tools"]
    if "general" in _ts:
        # general 参与组合 → sections/tools 取全量，extra 取其余类型的并集
        _wanted = set(_secs_order)
        _tools = None
        _extra = []
        for _t in _ts:
            for _e in _ENV_PROBE_TYPES[_t].get("extra") or []:
                if _e not in _extra:
                    _extra.append(_e)
    else:
        _wanted = set()
        _tools = []
        _extra = []
        for _t in _ts:
            _cfg = _ENV_PROBE_TYPES[_t]
            _wanted.update(_cfg["sections"])
            for _tt in _cfg.get("tools") or []:
                if _tt not in _tools:
                    _tools.append(_tt)
            for _e in _cfg.get("extra") or []:
                if _e not in _extra:
                    _extra.append(_e)
        if not _tools:
            _tools = None

    _secs = {
        "system": _env_section_system,
        "user": _env_section_user,
        "network": _env_section_network,
        "disk": _env_section_disk,
        "tools": lambda: _env_section_tools(_tools),
    }
    for _s in _secs_order:
        if _s not in _wanted:
            continue
        _lines_block = _secs[_s]()
        if _lines_block:
            lines += _lines_block
            lines.append("")
    # 类型专属探测（多类型时取并集）
    if _extra:
        lines.append(f"### 专属探测（{','.join(_ts)}）")
        for _label, _cmd in _extra:
            _out = _env_probe_run(_cmd)
            if _out:
                lines.append(f"- {_label}:\n{_out[:300]}")
        lines.append("")
    # 附加指定命令查询
    if (which or "").strip():
        _w = _env_probe_which_lines(which)
        if _w:
            lines += _w
            lines.append("")
    # ── 动态反思要点：仅当探测到实际缺口时提示，避免每轮重复静态反思 ──
    import shutil as _sh_tip
    _tips = []
    if not _sh_tip.which("ss") and _sh_tip.which("netstat"):
        _tips.append("ss 缺失 → 端口/连接查询改用 netstat")
    if not _sh_tip.which("ip") and _sh_tip.which("ifconfig"):
        _tips.append("ip 缺失 → 接口/路由查询改用 ifconfig")
    if not _sh_tip.which("grep"):
        _tips.append("grep 缺失（Windows 环境）→ 用 findstr 替代")
    if _tips:
        lines.append("> 反思要点：" + "；".join(_tips))
    return "\n".join(lines)
