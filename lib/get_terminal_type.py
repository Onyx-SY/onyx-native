# onyx/lib/get_terminal_type.py
"""
终端类型检测模块 v2
自动检测当前运行的终端类型（带1小时缓存）
支持硬编码优先级：bash > zsh > pwsh > fish > cmd
"""

import os
import sys
import platform
import time
import shutil


# ========== 缓存机制 ==========
_TERMINAL_TYPE_CACHE = None
_TERMINAL_TYPE_CACHE_TIME = 0
_CACHE_DURATION = 3600  # 1小时


def clear_terminal_type_cache():
    """清除终端类型缓存（用于测试或强制刷新）"""
    global _TERMINAL_TYPE_CACHE, _TERMINAL_TYPE_CACHE_TIME
    _TERMINAL_TYPE_CACHE = None
    _TERMINAL_TYPE_CACHE_TIME = 0


def get_terminal_type() -> str:
    """
    获取当前终端类型（带1小时缓存）
    
    硬编码优先级（按可用性检查）：
    1. bash
    2. zsh
    3. pwsh (PowerShell Core)
    4. fish
    5. powershell (Windows PowerShell)
    6. cmd (Windows only)
    7. sh (Unix fallback)
    
    返回：
    - 'bash': Bash shell
    - 'zsh': Zsh shell
    - 'fish': Fish shell
    - 'powershell': PowerShell (Core or Windows)
    - 'cmd': Windows CMD
    - 'sh': 默认 shell
    """
    global _TERMINAL_TYPE_CACHE, _TERMINAL_TYPE_CACHE_TIME
    
    now = time.time()
    
    # 检查缓存
    if _TERMINAL_TYPE_CACHE is not None and (now - _TERMINAL_TYPE_CACHE_TIME) < _CACHE_DURATION:
        return _TERMINAL_TYPE_CACHE
    
    result = _detect_terminal_type()
    
    # 更新缓存
    _TERMINAL_TYPE_CACHE = result
    _TERMINAL_TYPE_CACHE_TIME = now
    
    return result


def _detect_terminal_type() -> str:
    """
    实际执行终端类型检测
    
    硬编码优先级（按可用性检查）：
    bash > zsh > pwsh > fish > powershell > cmd/sh
    """
    
    # Windows 系统判断
    if sys.platform == 'win32':
        # 按优先级检查可用 shell
        if shutil.which("bash"):
            return 'bash'
        if shutil.which("zsh"):
            return 'zsh'
        if shutil.which("pwsh"):
            return 'powershell'
        if shutil.which("fish"):
            return 'fish'
        if shutil.which("powershell"):
            return 'powershell'
        return 'cmd'
    
    # Unix/Linux/Mac 系统判断
    # 按优先级检查可用 shell
    if shutil.which("bash"):
        return 'bash'
    if shutil.which("zsh"):
        return 'zsh'
    if shutil.which("pwsh"):
        return 'powershell'
    if shutil.which("fish"):
        return 'fish'
    
    # 检查 SHELL 环境变量作为备选
    shell_path = os.environ.get('SHELL', '')
    shell_name = os.path.basename(shell_path).lower()
    if shell_name in ['bash', 'zsh', 'fish', 'sh', 'dash', 'ash']:
        if shutil.which(shell_name):
            return shell_name
    
    # 检查父进程（使用 psutil 如果可用）
    try:
        import psutil
        process = psutil.Process()
        while process:
            proc_name = process.name().lower()
            if proc_name in ['bash', 'zsh', 'fish', 'sh', 'dash']:
                if shutil.which(proc_name):
                    return proc_name
            # 向上查找父进程
            process = process.parent() if process.pid != 1 else None
    except (ImportError, Exception):
        pass
    
    # 检查 /proc 文件系统（Linux）
    try:
        if os.path.exists('/proc/self/exe'):
            exe_path = os.readlink('/proc/self/exe')
            exe_name = os.path.basename(exe_path).lower()
            if exe_name in ['bash', 'zsh', 'fish', 'sh', 'dash']:
                if shutil.which(exe_name):
                    return exe_name
    except Exception:
        pass
    
    # 默认返回 sh
    return 'sh'


def get_terminal_version() -> str:
    """获取终端版本信息"""
    terminal_type = get_terminal_type()
    
    if terminal_type in ['bash', 'zsh', 'fish', 'sh']:
        import subprocess
        try:
            result = subprocess.run(
                [terminal_type, '--version'],
                capture_output=True,
                text=True,
                timeout=2
            )
            return result.stdout.split('\n')[0]
        except:
            return f"{terminal_type} (version unknown)"
    
    if terminal_type == 'powershell':
        import subprocess
        # 优先使用 pwsh，否则使用 powershell
        shell_cmd = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
        try:
            result = subprocess.run(
                [shell_cmd, '-Command', '$PSVersionTable.PSVersion.ToString()'],
                capture_output=True,
                text=True,
                timeout=2
            )
            return f"PowerShell {result.stdout.strip()}"
        except:
            return "PowerShell (version unknown)"
    
    if terminal_type == 'cmd':
        return "Windows CMD"
    
    return terminal_type


if __name__ == '__main__':
    print(f"Detected terminal type: {get_terminal_type()}")
    print(f"Terminal version: {get_terminal_version()}")
    
    # 测试缓存
    import time
    start = time.time()
    t1 = get_terminal_type()
    t2 = get_terminal_type()
    print(f"Cache test: same result={t1==t2}, time={time.time()-start:.6f}s")