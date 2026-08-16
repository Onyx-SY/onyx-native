"""pty 集成测试：模拟 AI 执行模式 + 终端 Ctrl+C 字节流"""
import os, pty, sys, time, select, signal


child_code = r'''
import os, signal, sys, time
sys.path.insert(0, ".")
from lib.terminal import exe

def _on_interrupt(signum, frame):
    print("[child] _on_interrupt: raise KeyboardInterrupt", flush=True)
    raise KeyboardInterrupt("User interrupted")

signal.signal(signal.SIGINT, _on_interrupt)
exe.AI_EXECUTION_MODE = True
try:
    rc = exe.run_cmd_sync("sleep 30", "req1", is_tool=True, AI_TOOL_OUTPUT_CACHE={}, cwd=None)
    print(f"[child] rc={rc}", flush=True)
except KeyboardInterrupt:
    print("[child] KeyboardInterrupt propagated", flush=True)
finally:
    exe.AI_EXECUTION_MODE = False
'''

pid, fd = pty.fork()
if pid == 0:

    os.execvp("python3", ["python3", "-c", child_code])

time.sleep(1.5)

os.write(fd, b"\x03")
time.sleep(1.0)


out = b""
while True:
    r, _, _ = select.select([fd], [], [], 0.5)
    if not r:
        break
    try:
        chunk = os.read(fd, 4096)
    except OSError:
        break
    if not chunk:
        break
    out += chunk

print("=== PTY output ===")
print(out.decode(errors="replace"))
try:
    os.close(fd)
except Exception:
    pass
