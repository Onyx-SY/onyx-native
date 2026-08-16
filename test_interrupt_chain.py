import os, signal, sys, time, threading
sys.path.insert(0, '.')
from lib.terminal import exe

def _on_interrupt(signum, frame):
    print("[handler] _on_interrupt: raise KeyboardInterrupt", flush=True)
    raise KeyboardInterrupt("User interrupted")

_orig = signal.getsignal(signal.SIGINT)
signal.signal(signal.SIGINT, _on_interrupt)

def _send_sigint():
    time.sleep(1.0)
    print("[main] sending SIGINT to self", flush=True)
    os.kill(os.getpid(), signal.SIGINT)

t = threading.Thread(target=_send_sigint, daemon=True)
t.start()

buf = []
try:
    rc = exe._exec_ai_subprocess("sleep 30", buf, None)
    print("[main] rc =", rc, flush=True)
    print("[main] buf =", buf, flush=True)
except KeyboardInterrupt:
    print("[main] KeyboardInterrupt propagated!", flush=True)
except Exception as e:
    print("[main] Exception:", type(e).__name__, e, flush=True)
finally:
    signal.signal(signal.SIGINT, _orig)
print("done", flush=True)
