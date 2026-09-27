# -*- coding: utf-8 -*-
"""PTY 实测：主 REPL 的虚影补全到底显示什么。

子进程：跑一个精简 universal_input 循环（种子历史 + 可注入环境变量），
父进程：向 PTY 发送按键，抓屏看光标后是否有虚影文字。

用法: python3 tmp/_ghost_pty.py "<按键字节转义>" [等待秒]
  例: python3 tmp/_ghost_pty.py "ech"       # 历史命中 → 期望虚影 o hello-world
      python3 tmp/_ghost_pty.py "gi"        # 历史命中
      python3 tmp/_ghost_pty.py "l"         # 无历史命中 → 期望回退到补全首项
"""
import fcntl
import importlib.util
import os
import pty
import select
import struct
import sys
import termios
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLS, ROWS = 70, 20
KEYS = (sys.argv[1] if len(sys.argv) > 1 else "ech").encode().decode("unicode_escape").encode()
WAIT = float(sys.argv[2]) if len(sys.argv) > 2 else 3.5

_argv = list(sys.argv)
sys.argv = ["tui_sim.py"]
_spec = importlib.util.spec_from_file_location("_sim", os.path.join(ROOT, "tmp", "tui_sim.py"))
_sim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sim)
sys.argv = _argv
Screen = _sim.Screen


def child():
    sys.path.insert(0, ROOT)
    os.environ.setdefault("TERM", "xterm-256color")
    from lib.terminal import input_lib as il
    il._HISTORY_INITIALIZED = True
    il._HISTORY_BUFFER[:] = ["echo hello-world", "git status", "grep -rn foo ."]
    from lib.terminal.com import set_other_terminal_cmds_path
    set_other_terminal_cmds_path(os.path.join(ROOT, "etc", "other_terminal_cmd.json"))
    # 诊断：把补全候选写文件（stderr 会进 PTY 干扰抓屏）
    try:
        from prompt_toolkit.document import Document
        from lib.terminal.com import SmartCompleter, SmartAutoSuggest

        class _FB:
            complete_state = None

        _cmds = sorted(il._get_terminal_specific_commands()) + ["sudo", "sado"]
        _sc = SmartCompleter(_cmds, history_buffer=il._HISTORY_BUFFER, user_home_dir="")
        _sug = SmartAutoSuggest(_sc)
        with open(os.path.join(ROOT, "tmp", "_ghost_diag.txt"), "w") as f:
            f.write(f"cmd_list({len(_cmds)})[:40]={_cmds[:40]}\n")
            for t in ("l", "ec", "xyz"):
                d = Document(text=t, cursor_position=len(t))
                f.write(f"{t!r}: comps={[c.text for c in _sc.get_completions(d, None)][:5]} "
                        f"ghost={getattr(_sug.get_suggestion(_FB(), d), 'text', None)!r}\n")
    except Exception as e:
        try:
            with open(os.path.join(ROOT, "tmp", "_ghost_diag.txt"), "w") as f:
                f.write(f"diag error: {e!r}\n")
        except Exception:
            pass
    try:
        while True:
            il.universal_input(prompt_func=lambda: "> ", user_home_dir="", language="chinese",
                               other_terminal_cmd_path=os.path.join(ROOT, "etc", "other_terminal_cmd.json"))
    except (KeyboardInterrupt, EOFError):
        pass


def main():
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
    pid = os.fork()
    if pid == 0:
        try:
            os.close(master)
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
            os.dup2(slave, 0); os.dup2(slave, 1); os.dup2(slave, 2)
            if slave > 2:
                os.close(slave)
            child()
        except BaseException:
            import traceback
            traceback.print_exc()
        finally:
            os._exit(0)
    os.close(slave)
    chunks = []
    t0 = time.time()
    sent = False
    while time.time() - t0 < WAIT:
        r, _, _ = select.select([master], [], [], 0.05)
        if r:
            try:
                d = os.read(master, 65536)
            except OSError:
                break
            if not d:
                break
            chunks.append(d)
        if not sent and time.time() - t0 > 1.5:
            os.write(master, KEYS)
            sent = True
    data = b"".join(chunks)
    sc = Screen(ROWS, COLS)
    sc.feed(data)
    print(f"=== 输入 {KEYS!r} 后的屏幕（{COLS}x{ROWS}）===")
    print(sc.dump())
    print("=== 输入行判定 ===")
    for i, row in enumerate(sc.buf):
        line = "".join(row)
        if ">" in line and line.strip().startswith(">"):
            print(f"  row{i}: {line!r}")
    try:
        os.kill(pid, 9)
    except Exception:
        pass


if __name__ == "__main__":
    main()
