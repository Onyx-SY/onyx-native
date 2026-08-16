import os, signal, sys, time, threading
sys.path.insert(0, '.')
from lib.terminal import exe


def _on_interrupt(signum, frame):
    print("[handler] _on_interrupt: raise KeyboardInterrupt", flush=True)
    raise KeyboardInterrupt("User interrupted")

_orig = signal.getsignal(signal.SIGINT)
signal.signal(signal.SIGINT, _on_interrupt)

def _send_sigint():
    time.sleep(1.2)
    print("[main] sending SIGINT to self", flush=True)
    os.kill(os.getpid(), signal.SIGINT)

t = threading.Thread(target=_send_sigint, daemon=True)
t.start()

exe.AI_EXECUTION_MODE = True
exe.AI_LAST_EXIT_CODE = None
buf = []
try:
    rc = exe.run_cmd_sync("sleep 30", "req1", is_tool=True, AI_TOOL_OUTPUT_CACHE={}, cwd=None)
    print("[main] rc =", rc, flush=True)
    print("[main] AI_LAST_EXIT_CODE =", exe.AI_LAST_EXIT_CODE, flush=True)
except KeyboardInterrupt:
    print("[main] KeyboardInterrupt propagated!", flush=True)
except Exception as e:
    print("[main] Exception:", type(e).__name__, e, flush=True)
finally:
    exe.AI_EXECUTION_MODE = False
    signal.signal(signal.SIGINT, _orig)
print("done", flush=True)
