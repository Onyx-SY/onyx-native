# -*- coding: utf-8 -*-
"""真实 PTY 下运行 TUI，抓取屏幕，检查状态栏是否真的可见。

用法: python3 tmp/_tui_pty_shot.py [cols] [rows] [秒数]
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
COLS = int(sys.argv[1]) if len(sys.argv) > 1 else 44
ROWS = int(sys.argv[2]) if len(sys.argv) > 2 else 20
WAIT = float(sys.argv[3]) if len(sys.argv) > 3 else 6.0

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
    import uuid
    from bin.ai_tui import _build_tui
    from bin.ai_lib import mode as _mode
    _mode.set_render_mode("tui")
    App = _build_tui()
    ctx = {"lang": "chinese", "session_id": str(uuid.uuid4()), "memory_mode": "global",
           "cwd": os.getcwd(), "quiet": False, "show_time": True,
           "session_start": time.time(), "mode": "normal"}
    app = App({"user_home_dir": "/tmp"}, ctx)

    if os.environ.get("PTY_INJECT"):
        import threading

        def _inj():
            time.sleep(2.0)
            try:
                app.call_from_thread(app._render_status, {
                    "cwd": "/data/data/com.termux/files/home/proj",
                    "ctx": 17214, "cache_pct": 88.4, "cache_supported": True,
                    "balance": "12.34 CNY", "balance_platform": "deepseek"})
            except Exception as e:
                print("INJ-ERR", e, file=sys.stderr)
            time.sleep(1.0)
            try:
                app.call_from_thread(app._set_thinking, True)
                time.sleep(1.0)
                app.call_from_thread(app._set_thinking, False)
            except Exception as e:
                print("INJ-ERR2", e, file=sys.stderr)

        threading.Thread(target=_inj, daemon=True).start()

    app.run()


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
    data = b"".join(chunks)
    idx = data.rfind(b"\x1b[?1049l")
    snap = Screen(ROWS, COLS)
    snap.feed(data[:idx] if idx > 0 else data)
    print(f"=== PTY 屏幕快照 {COLS}x{ROWS}，{WAIT}s（TUI 运行期间）===")
    print(snap.dump())
    print("=== 关键行判定 ===")
    for i, row in enumerate(snap.buf):
        line = "".join(row).rstrip()
        if "◆" in line and i > ROWS // 2:
            print(f"  row{i}: {line!r}")
    try:
        os.kill(pid, 9)
    except Exception:
        pass


if __name__ == "__main__":
    main()
