# -*- coding: utf-8 -*-
"""批量修复 exe.py 的既有问题（一次性替换，输出最小）。"""
p = 'lib/terminal/exe.py'
s = open(p, encoding='utf-8').read()


def rep(a, b, cnt=1):
    global s
    n = s.count(a)
    assert n == cnt, (a[:50], n)
    s = s.replace(a, b)


# M7 可调超时常量
rep("_current_pty_size = (24, 80)\n_shell_lock = threading.Lock()",
    "_current_pty_size = (24, 80)\n_shell_lock = threading.Lock()\n\n"
    "# 可调超时（环境变量可覆盖，便于高负载 / 慢设备调优）\n"
    "_ECHO_SKIP_TIMEOUT = float(os.environ.get('ONYX_ECHO_TIMEOUT', '0.6'))\n"
    "_WRITE_TOTAL_TIMEOUT = float(os.environ.get('ONYX_WRITE_TIMEOUT', '10'))\n"
    "_SHELL_READY_TIMEOUT = float(os.environ.get('ONYX_SHELL_READY_TIMEOUT', '5'))")

# M2 echo 跳过超时不再硬编码 0.2s
rep("if _echo_buf and time.time() - _echo_start_time > 0.2:",
    "if _echo_buf and time.time() - _echo_start_time > _ECHO_SKIP_TIMEOUT:")

# M3 drain 静默窗口 2ms -> 10ms（高负载下 2ms 太短，会提前结束读取）
rep("def _drain_output(self, max_iterations: int = 8, quiet_timeout: float = 0.002):",
    "def _drain_output(self, max_iterations: int = 8, quiet_timeout: float = 0.01):")

# M6 shell ready 超时可配置
rep("while time.time() - start_time < 5.0:",
    "while time.time() - start_time < _SHELL_READY_TIMEOUT:")

# M1 _write_to_master 总超时（shell 卡死时不再永久挂住调用方）
rep("""            mv = memoryview(data)
            while mv:
                try:
                    n = os.write(self.master_fd, mv)""",
    """            mv = memoryview(data)
            # 总超时兜底：shell 被 SIGSTOP / 卡死时不至于把调用方永久挂住
            _wdeadline = time.time() + _WRITE_TOTAL_TIMEOUT
            while mv:
                if time.time() > _wdeadline:
                    debug_log(f"write_to_master timeout, {len(mv)} bytes dropped", 'error')
                    break
                try:
                    n = os.write(self.master_fd, mv)""")
rep("                        select.select([], [self.master_fd], [], 0.5)",
    "                        select.select([], [self.master_fd], [], 0.1)")

# M4 交互式白名单补全（此前 emacs/nvim/psql/tmux 等需要 TTY 的命令会走无 TTY 的 subprocess）
rep("""_AI_INTERACTIVE_TOKENS = frozenset({
    "vim", "vi", "nano", "top", "htop", "less", "more", "watch",
    "ssh", "telnet", "ftp", "sftp", "mc", "ranger",
})""",
    """_AI_INTERACTIVE_TOKENS = frozenset({
    "vim", "vi", "nvim", "nano", "emacs", "top", "htop", "btop", "less",
    "more", "watch", "man", "ssh", "telnet", "ftp", "sftp", "mc", "ranger",
    "screen", "tmux", "fzf", "psql", "mysql", "sqlite3", "redis-cli",
    "gdb", "lldb", "dialog", "whiptail",
})""")

# M5 AI 命令超时后回收子进程（防僵尸）
rep("""    except _sp.TimeoutExpired:
        _kill_cmd_process()""",
    """    except _sp.TimeoutExpired:
        _kill_cmd_process()
        try:
            _proc.wait(timeout=5)      # 回收，避免僵尸
        except Exception:
            pass""")

open(p, 'w', encoding='utf-8').write(s)
print('ALL REPLACED')
