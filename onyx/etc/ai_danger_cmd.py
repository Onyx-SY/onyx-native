# onyx/etc/ai_danger_cmd.py
"""
AI危险命令初始化模块
存储和管理可能危险的命令列表
"""

import os
import json
from typing import List, Set

# 默认危险命令列表
DEFAULT_DANGEROUS_COMMANDS = [
    "rm",
    "mv",
    "echo",  # echo > 重定向可能危险
    "dd", 
    "mkfs",
    "fdisk",
    "format",
    "shutdown",
    "reboot",
    "init",
    "chmod",
    "chown",
    "kill",
    "pkill",
    "killall"
]

def init_ai_dangerous_commands(USER_HOME_DIR: str, log_info=None) -> bool:
    """
    初始化AI危险命令配置文件
    :param USER_HOME_DIR: 用户主目录
    :param log_info: 日志函数
    :return: 是否成功
    """
    danger_file = os.path.join(USER_HOME_DIR, ".ai_dangerous.txt")
    
    try:
        # 如果文件不存在，创建默认配置
        if not os.path.exists(danger_file):
            with open(danger_file, "w", encoding="utf-8") as f:
                for cmd in DEFAULT_DANGEROUS_COMMANDS:
                    f.write(f"{cmd}\n")
            
            # 设置文件权限（仅所有者可读写）
            if os.name == "posix":
                os.chmod(danger_file, 0o600)
            
            if log_info:
                log_info(f"AI危险命令配置文件初始化完成：{danger_file} (共{len(DEFAULT_DANGEROUS_COMMANDS)}条默认命令)", "ai_danger_init")
        
        return True
        
    except Exception as e:
        if log_info:
            log_info(f"AI危险命令配置文件初始化失败：{str(e)}", "ai_danger_init")
        return False

def load_ai_dangerous_commands(USER_HOME_DIR: str, log_info=None) -> Set[str]:
    """
    加载AI危险命令列表
    :param USER_HOME_DIR: 用户主目录
    :param log_info: 日志函数
    :return: 危险命令集合
    """
    danger_file = os.path.join(USER_HOME_DIR, ".ai_dangerous.txt")
    dangerous_commands = set(DEFAULT_DANGEROUS_COMMANDS)  # 默认集合
    
    try:
        if os.path.exists(danger_file):
            with open(danger_file, "r", encoding="utf-8") as f:
                # 读取非注释、非空行
                commands = {line.strip().lower() for line in f 
                          if line.strip() and not line.startswith("#")}
                if commands:
                    dangerous_commands = commands
            
            if log_info:
                log_info(f"AI危险命令列表加载完成：共{len(dangerous_commands)}条", "ai_danger_load")
        
        return dangerous_commands
        
    except Exception as e:
        if log_info:
            log_info(f"AI危险命令列表加载失败：{str(e)}，使用默认列表", "ai_danger_load")
        return dangerous_commands