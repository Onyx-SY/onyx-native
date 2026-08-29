# -*- coding: utf-8 -*-
"""
env_probe.py — EnvProbe 环境探测工具（只读，秒回）

从 bin/ai_cmd.py 拆分（模块化架构重构）：
- 标准库依赖 + 局部 import，无 ai_cmd 闭包依赖；
- _ENV_PROBE_TYPES 配置驱动：type 决定探测范围（sections）+ 工具子集 + 专属探测。
- 增强跨平台支持（Windows/Linux/macOS/Cygwin/MSYS2）
"""

import os
import re
import sys
import platform
from typing import List, Optional, Dict, Any, Tuple


# ── 跨平台工具别名映射 ──
_PLATFORM_ALIASES = {
    "linux": {
        "ifconfig": "ip addr",
        "route": "ip route",
        "netstat": "ss -tuln",
        "nmap": "nmap",
    },
    "windows": {
        "ifconfig": "ipconfig",
        "route": "route print",
        "netstat": "netstat -ano",
        "ping": "ping -n 4",
        "grep": "findstr",
        "curl": "curl.exe",
        "wget": "wget.exe",
        "python": "python.exe",
        "pip": "pip.exe",
        "git": "git.exe",
        "tar": "tar.exe",
        "unzip": "tar.exe -xf",  # Windows tar 支持解压 zip
        "gzip": "tar.exe -xzf",
    },
    "darwin": {
        "ifconfig": "ifconfig",
        "route": "netstat -rn",
        "netstat": "netstat -an",
    }
}

# ── 跨平台命令查找后缀 ──
_WINDOWS_EXTS = ('.exe', '.bat', '.cmd', '.ps1', '.com')
_CYGWIN_EXTS = ('.exe', '.bat', '.cmd', '.com')


def _get_platform_key() -> str:
    """获取平台标识：linux / windows / darwin / cygwin / msys"""
    if sys.platform.startswith("win") or os.name == "nt":
        # 检测是否为 Cygwin/MSYS 环境
        if "CYGWIN" in platform.system() or "MSYS" in platform.system():
            return "cygwin"
        return "windows"
    elif sys.platform.startswith("darwin"):
        return "darwin"
    else:
        return "linux"


def _env_probe_run(cmd: str, timeout: int = 3) -> str:
    """EnvProbe 内部探测：subprocess 快速执行，失败静默。增强跨平台健壮性。"""
    import subprocess as _sp
    
    # Windows 下优先使用 PowerShell 兼容命令
    if _get_platform_key() == "windows" and not cmd.startswith("powershell"):
        # 某些命令在 Windows 下需要特殊处理
        if cmd.startswith("ip addr"):
            cmd = "ipconfig"
        elif cmd.startswith("ifconfig") and "ipconfig" not in cmd:
            cmd = "ipconfig" if "ip" not in cmd else cmd
        elif cmd.startswith("route"):
            cmd = "route print"
    
    try:
        _r = _sp.run(
            cmd, 
            shell=True, 
            capture_output=True, 
            text=True,
            errors="replace", 
            timeout=timeout,
            # Windows 下避免编码问题
            encoding="utf-8" if _get_platform_key() != "windows" else "gbk"
        )
        # 合并 stdout/stderr，去除多余空白
        output = ((_r.stdout or "").strip() + "\n" + (_r.stderr or "").strip()).strip()
        # 清理 Windows 下的回车换行
        if _get_platform_key() == "windows":
            output = output.replace('\r\n', '\n')
        return output
    except _sp.TimeoutExpired:
        return ""
    except UnicodeDecodeError:
        # 编码问题降级处理
        try:
            _r = _sp.run(cmd, shell=True, capture_output=True, timeout=timeout)
            return (_r.stdout or b"").decode("utf-8", errors="ignore").strip()[:500]
        except Exception:
            return ""
    except Exception:
        return ""


def _shutil_which(cmd: str) -> Optional[str]:
    """跨平台 shutil.which 包装，支持 Windows 扩展名检测"""
    import shutil as _sh
    
    # 直接查找
    path = _sh.which(cmd)
    if path:
        return path
    
    # Windows 下尝试添加扩展名
    if _get_platform_key() == "windows":
        for ext in _WINDOWS_EXTS:
            if not cmd.endswith(ext):
                path = _sh.which(cmd + ext)
                if path:
                    return path
    
    return None


def _has_command(cmd: str) -> bool:
    """跨平台检查命令是否存在"""
    return bool(_shutil_which(cmd))


def _env_section_system() -> List[str]:
    """系统信息探测 - 增强跨平台"""
    lines = ["### 系统"]
    
    # 基础系统信息
    system = platform.system()
    release = platform.release()
    version = platform.version()
    
    lines.append(f"- OS: {system} {release}")
    if version and len(version) < 80:
        lines.append(f"- 版本: {version[:80]}")
    
    lines.append(f"- 架构: {platform.machine() or platform.processor() or '未知'}")
    lines.append(f"- Python: {platform.python_version()}")
    lines.append(f"- 解释器: {sys.executable}")
    lines.append(f"- 平台: {sys.platform}")
    
    # 平台特定信息
    if _get_platform_key() == "windows":
        # Windows 特有信息
        win_ver = _env_probe_run("ver 2>nul")
        if win_ver:
            lines.append(f"- Windows: {win_ver[:80]}")
        # 查看系统盘
        sys_drive = os.environ.get("SystemDrive", "C:")
        lines.append(f"- 系统盘: {sys_drive}")
    else:
        # Unix-like 系统
        _uname = _env_probe_run("uname -a 2>/dev/null")
        if _uname:
            lines.append(f"- uname: {_uname[:140]}")
        
        # 内核版本
        kernel = _env_probe_run("uname -r 2>/dev/null")
        if kernel:
            lines.append(f"- 内核: {kernel[:60]}")
        
        # 发行版信息
        if _has_command("lsb_release"):
            distro = _env_probe_run("lsb_release -ds 2>/dev/null")
            if distro:
                lines.append(f"- 发行版: {distro[:80]}")
        elif os.path.exists("/etc/os-release"):
            distro = _env_probe_run("grep PRETTY_NAME /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '\"'")
            if distro:
                lines.append(f"- 发行版: {distro[:80]}")
    
    # 环境变量关键路径
    path = os.environ.get("PATH", "")
    lines.append(f"- PATH长度: {len(path)} 字符")
    
    return lines


def _env_section_user() -> List[str]:
    """用户信息探测 - 增强跨平台"""
    import getpass as _gp
    
    lines = ["### 用户与权限"]
    
    # 当前用户
    try:
        user = _gp.getuser()
        lines.append(f"- 用户: {user}")
    except Exception:
        lines.append("- 用户: 无法获取")
    
    # Windows 特有
    if _get_platform_key() == "windows":
        # 管理员检测
        try:
            import ctypes
            is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
            lines.append(f"- 权限: {'✅ 管理员' if is_admin else '⚠️ 普通用户'}")
        except Exception:
            lines.append("- 权限: 无法检测")
        # 域信息
        domain = os.environ.get("USERDOMAIN", "")
        if domain:
            lines.append(f"- 域: {domain}")
    else:
        # Unix 权限检测
        is_root = hasattr(os, "geteuid") and os.geteuid() == 0
        lines.append(f"- 权限: {'✅ root（可执行特权操作）' if is_root else '⚠️ 普通用户（非 root）'}")
        
        # 用户组信息
        groups = _env_probe_run("groups 2>/dev/null")
        if groups:
            lines.append(f"- 组: {groups[:80]}")
    
    # 通用信息
    try:
        lines.append(f"- 工作目录: {os.getcwd()}")
    except Exception:
        lines.append("- 工作目录: 无法获取")
    
    lines.append(f"- 用户目录: {os.path.expanduser('~')}")
    
    shell = os.environ.get('SHELL') or os.environ.get('ComSpec') or os.environ.get('SHELL')
    if shell:
        lines.append(f"- Shell: {shell}")
    
    lang = os.environ.get("LANG") or os.environ.get("LC_ALL") or ""
    if lang:
        lines.append(f"- locale: {lang}")
    
    # 终端类型
    term = os.environ.get("TERM") or "未知"
    lines.append(f"- 终端: {term}")
    
    return lines


def _env_section_network() -> List[str]:
    """网络信息探测 - 增强跨平台和容错"""
    lines = ["### 网络"]
    platform_key = _get_platform_key()
    
    # 网络接口信息
    if platform_key == "windows":
        # Windows 使用 ipconfig
        iface = _env_probe_run("ipconfig /all 2>nul | findstr /i \"适配器\\|IPv4\\|IPv6\"")
        if iface:
            lines.append(f"- 接口/地址:\n{iface[:500]}")
        else:
            lines.append("- 接口: 无法枚举")
    else:
        # Unix-like 使用 ip 或 ifconfig
        if _has_command("ip"):
            iface = _env_probe_run("ip -o addr 2>/dev/null | grep -v ' lo ' | head -5")
            if iface:
                lines.append(f"- 接口/地址:\n{iface[:500]}")
            else:
                lines.append("- 接口: 无活动接口")
        elif _has_command("ifconfig"):
            iface = _env_probe_run("ifconfig 2>/dev/null | grep -E '^(eth|wlan|en|wl|br|docker|virbr)|inet ' | head -12")
            if iface:
                lines.append(f"- 接口/地址:\n{iface[:500]}")
            else:
                lines.append("- 接口: 无法枚举（ifconfig 无输出）")
        else:
            lines.append("- 接口: 无 ip/ifconfig 命令")
    
    # 路由信息 - 跨平台
    if platform_key == "windows":
        route = _env_probe_run("route print -4 2>nul | findstr \"0.0.0.0\"")
        if route:
            lines.append(f"- 路由:\n{route[:300]}")
        else:
            lines.append("- 路由: 无法读取")
    elif _has_command("ip"):
        route = _env_probe_run("ip route 2>/dev/null | head -4")
        if route:
            lines.append(f"- 路由:\n{route[:300]}")
        else:
            lines.append("- 路由: 无法读取")
    elif _has_command("route"):
        route = _env_probe_run("route -n 2>/dev/null | head -6")
        if route:
            lines.append(f"- 路由:\n{route[:300]}")
        else:
            lines.append("- 路由: 无法读取")
    else:
        lines.append("- 路由: 无 route 命令")
    
    # DNS 信息（跨平台）
    if platform_key == "windows":
        dns = _env_probe_run("nslookup localhost 2>nul | findstr Address")
        if dns:
            lines.append(f"- DNS: {dns[:200]}")
    else:
        dns = _env_probe_run("cat /etc/resolv.conf 2>/dev/null | grep nameserver | head -3")
        if dns:
            lines.append(f"- DNS:\n{dns[:200]}")
    
    # 网络连通性基本测试
    ping_cmd = "ping -n 1 -w 1000 8.8.8.8" if platform_key == "windows" else "ping -c 1 -W 1 8.8.8.8 2>/dev/null"
    ping_result = _env_probe_run(ping_cmd, timeout=2)
    if ping_result and ("reply" in ping_result.lower() or "ms" in ping_result.lower()):
        lines.append("- 外网连通: ✅ 可 ping 通 8.8.8.8")
    else:
        lines.append("- 外网连通: ❌ 无法 ping 通 8.8.8.8")
    
    return lines


def _env_section_disk() -> List[str]:
    """磁盘信息探测 - 增强跨平台"""
    lines = ["### 磁盘"]
    platform_key = _get_platform_key()
    
    if platform_key == "windows":
        # Windows 使用 wmic 或 fsutil
        disks = _env_probe_run("wmic logicaldisk get size,freespace,caption 2>nul | findstr /v \"Caption\"")
        if disks:
            lines.append(f"```\n{disks[:400]}\n```")
        else:
            # 备用：检查所有盘符
            import string
            disk_info = []
            for drive in string.ascii_uppercase:
                drive_path = f"{drive}:\\"
                if os.path.exists(drive_path):
                    try:
                        # Windows 下获取磁盘空间
                        import ctypes
                        free_bytes = ctypes.c_ulonglong(0)
                        total_bytes = ctypes.c_ulonglong(0)
                        if ctypes.windll.kernel32.GetDiskFreeSpaceExW(
                            ctypes.c_wchar_p(drive_path),
                            ctypes.byref(free_bytes),
                            ctypes.byref(total_bytes),
                            None
                        ):
                            free_gb = free_bytes.value / (1024**3)
                            total_gb = total_bytes.value / (1024**3)
                            disk_info.append(f"{drive}: {total_gb:.1f}GB 总, {free_gb:.1f}GB 空闲")
                        else:
                            disk_info.append(f"{drive}: 存在")
                    except Exception:
                        disk_info.append(f"{drive}: 存在（无法获取详情）")
            if disk_info:
                lines.append(f"```\n{chr(10).join(disk_info[:8])}\n```")
            else:
                lines.append("- 磁盘: 无法获取信息")
    else:
        # Unix-like 使用 df
        df = _env_probe_run("df -h 2>/dev/null | head -6")
        if df:
            lines.append(f"```\n{df}\n```")
        else:
            lines.append("- 磁盘: 无法获取信息（df 不可用）")
    
    # 额外磁盘信息
    if platform_key != "windows":
        mounts = _env_probe_run("mount 2>/dev/null | head -5")
        if mounts:
            lines.append(f"- 挂载点:\n{mounts[:200]}")
    
    return lines


def _env_section_tools(tools: Optional[List[str]] = None) -> List[str]:
    """工具可用性探测 - 增强跨平台"""
    _list = tools if tools else _ENV_PROBE_TOOLS
    _avail, _missing = [], []
    platform_key = _get_platform_key()
    
    for _t in _list:
        # 根据平台调整命令名
        cmd_name = _t
        if platform_key == "windows":
            # Windows 特定命令映射
            alias_map = _PLATFORM_ALIASES.get("windows", {})
            if _t in alias_map:
                # 对于别名，检查原始命令和别名
                if _shutil_which(_t) or _shutil_which(alias_map[_t].split()[0]):
                    _avail.append(_t)
                    continue
        if _shutil_which(cmd_name):
            _avail.append(_t)
        else:
            _missing.append(_t)
    
    # 限制显示长度
    avail_str = ', '.join(_avail) if len(_avail) <= 50 else ', '.join(_avail[:50]) + f" ... 共{len(_avail)}个"
    missing_str = ', '.join(_missing) if len(_missing) <= 50 else ', '.join(_missing[:50]) + f" ... 共{len(_missing)}个"
    
    return ["### 命令可用性",
            f"- ✅ 可用 ({len(_avail)}): {avail_str}",
            f"- ❌ 缺失 ({len(_missing)}): {missing_str}"]


# which 参数允许的命令名字符（增加 Windows 兼容）
_ENV_WHICH_NAME_RE = re.compile(r"^[A-Za-z0-9_\-\.\+/:]+$")


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
    import subprocess as _sp
    _cmds = [c.strip() for c in re.split(r"[,，\s]+", which or "") if c.strip()]
    if not _cmds:
        return []
    lines = ["### 指定命令查询"]
    platform_key = _get_platform_key()
    
    for _c in _cmds[:10]:  # 上限 10 个，防滥用
        if not _ENV_WHICH_NAME_RE.fullmatch(_c):
            lines.append(f"- ⚠️ {_c[:40]}: 非法命令名（仅支持单个命令名，不能带参数）")
            continue
        
        # 查找命令路径（跨平台）
        _p = _shutil_which(_c)
        if not _p:
            # 尝试 Windows 别名
            if platform_key == "windows":
                alias = _PLATFORM_ALIASES["windows"].get(_c)
                if alias:
                    alias_cmd = alias.split()[0]
                    _p = _shutil_which(alias_cmd) or _shutil_which(alias_cmd + ".exe")
                    if _p:
                        lines.append(f"- ✅ {_c}: {_p} (别名: {alias})")
                        continue
            lines.append(f"- ❌ {_c}: 未找到（PATH 中不存在）")
            continue
        
        # 获取版本信息
        _ver = ""
        for _flag in ("--version", "-V", "-v"):
            try:
                # Windows 某些命令需要特殊处理
                if platform_key == "windows" and _c in ("curl", "wget"):
                    _flag = "--version"
                _r = _sp.run([_p, _flag], capture_output=True, text=True,
                             errors="replace", timeout=2,
                             encoding="utf-8" if platform_key != "windows" else "gbk")
                _out = ((_r.stdout or "").strip() + " " + (_r.stderr or "").strip()).strip()
                if _out:
                    _ver = _out.splitlines()[0][:80]
                    break
            except UnicodeDecodeError:
                try:
                    _r = _sp.run([_p, _flag], capture_output=True, timeout=2)
                    _out = (_r.stdout or b"").decode("utf-8", errors="ignore").strip()
                    if _out:
                        _ver = _out.splitlines()[0][:80]
                        break
                except Exception:
                    continue
            except Exception:
                continue
        
        if _ver:
            lines.append(f"- ✅ {_c}: {_p}（{_ver}）")
        else:
            lines.append(f"- ✅ {_c}: {_p}")
    
    return lines


# ── EnvProbe 任务类型配置（增强跨平台） ──
_ENV_PROBE_TOOLS = [
    # 基础工具（跨平台）
    "python3", "python", "pip", "pip3", "git", "curl", "wget",
    # 网络工具
    "nmap", "netstat", "ss", "ping", "ifconfig", "ip", "arp", "lsof", "fuser",
    # 压缩工具
    "tar", "unzip", "gzip",
    # 编译工具
    "gcc", "make", "node", "npm", "npx", "java", "go",
    # 容器/数据库
    "docker", "kubectl", "sqlite3", "redis-cli", "mysql", "psql",
    # DNS 工具
    "dig", "nslookup", "host",
    # 安全/编码工具
    "openssl", "base64", "xxd", "od", "hexdump", "jq", "nc", "socat",
    "tshark", "tcpdump", "msfconsole", "hydra", "sqlmap", "nikto",
    "gobuster", "ffuf", "john", "hashcat",
    # Shell
    "bash", "zsh", "fish", "sh",
    # Windows 特定
    "powershell", "cmd", "tasklist",
]


# 配置中的工具列表同步增强（保持兼容性）
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
        "extra": [("内存", "free -h 2>/dev/null | head -3") if _get_platform_key() != "windows" 
                  else ("内存", "wmic os get FreePhysicalMemory,TotalVisibleMemorySize 2>nul | findstr /v \"Free\"")],
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
        "extra": [("监听端口", "ss -tln 2>/dev/null | head -10 || netstat -tln 2>/dev/null | head -10")],
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
    # ── 动态反思要点：增强跨平台提示 ──
    _tips = []
    platform_key = _get_platform_key()
    
    if not _shutil_which("ss") and _shutil_which("netstat"):
        _tips.append("ss 缺失 → 端口/连接查询改用 netstat")
    if not _shutil_which("ip") and _shutil_which("ifconfig"):
        _tips.append("ip 缺失 → 接口/路由查询改用 ifconfig")
    if not _shutil_which("grep"):
        if platform_key == "windows":
            _tips.append("grep 缺失（Windows 环境）→ 用 findstr 替代")
        else:
            _tips.append("grep 缺失 → 搜索结果可能受限")
    
    # 平台特定建议
    if platform_key == "windows":
        if not _shutil_which("wget") and _shutil_which("curl"):
            _tips.append("wget 缺失 → 下载可用 curl 替代")
        if not _shutil_which("bash"):
            _tips.append("bash 缺失 → Windows 环境建议使用 PowerShell")
    elif platform_key == "darwin":
        _tips.append("macOS 环境 → 注意 BSD 工具与 GNU 工具的差异")
    
    if _tips:
        lines.append("> 反思要点：" + "；".join(_tips))
    
    return "\n".join(lines)