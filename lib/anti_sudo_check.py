import os
import sys
import pwd
import grp

def anti_sudo_check():
    """
    超强防sudo运行检测
    合法：原生root登录、普通用户非提权运行
    非法：sudo / sudo su / 提权切换 / 环境伪造 全部拦截
    """
    # 1. 真实UID与有效UID不一致 = sudo提权
    real_uid = os.getuid()
    eff_uid = os.geteuid()
    real_gid = os.getgid()
    eff_gid = os.getegid()

    if real_uid != eff_uid or real_gid != eff_gid:
        print("Error: You are not allowed to run this program with sudo.")
        sys.exit(1)

    # 2. 检测sudo专属环境变量
    sudo_env_list = [
        "SUDO_UID", "SUDO_GID", "SUDO_USER",
        "SUDO_COMMAND", "SUDO_PID", "SUDO_LOGNAME"
    ]
    for env in sudo_env_list:
        if env in os.environ:
            print("Error: You are not allowed to run this program with sudo.")
            sys.exit(1)

    # 3. 用户名映射校验，防止UID伪造
    try:
        username = pwd.getpwuid(real_uid).pw_name
        groupname = grp.getgrgid(real_gid).gr_name
    except Exception:
        print("Error: Illegal permission environment detected.")
        sys.exit(1)

    # 4. root原生登录放行，sudo-root一律拒绝
    if real_uid == 0:
        if any(key in os.environ for key in sudo_env_list):
            print("Error: You are not allowed to run this program with sudo.")
            sys.exit(1)

    # 5. 检测父进程是否为sudo/su程序
    try:
        ppid = os.getppid()
        with open(f"/proc/{ppid}/comm", "r", encoding="utf-8") as f:
            parent_process = f.read().strip().lower()
        if "sudo" in parent_process or "su" in parent_process:
            print("Error: You are not allowed to run this program with sudo.")
            sys.exit(1)
    except Exception:
        pass

    return True

# 程序入口
if __name__ == "__main__":
    anti_sudo_check()

    print("Program running normally")
