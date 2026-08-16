import os, pty, sys, time, select

child_code = r'''
import sys
sys.path.insert(0, ".")
from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.key_binding.defaults import load_key_bindings
from prompt_toolkit.filters import is_searching

kb = KeyBindings()

@kb.add('enter', eager=True, filter=~is_searching)
@kb.add('c-j', eager=True, filter=~is_searching)
def _submit(event):
    print("[child] SUBMIT", flush=True)
    event.current_buffer.validate_and_handle()

@kb.add('escape', 'enter', eager=True, filter=~is_searching)
@kb.add('escape', 'c-j', eager=True, filter=~is_searching)
def _newline(event):
    print("[child] NEWLINE", flush=True)
    event.current_buffer.insert_text('\n')

@kb.add('c-c', eager=True, filter=~is_searching)
def _cancel(event):
    print("[child] CANCEL", flush=True)
    event.current_buffer.reset()

@kb.add('escape', filter=~is_searching)
def _esc_exit(event):
    print("[child] ESC_EXIT", flush=True)
    event.app.exit(exception=EOFError())

kb2 = merge_key_bindings([load_key_bindings(), kb])
s = PromptSession("> ", multiline=True, key_bindings=kb2)
try:
    r = s.prompt()
    print("[child] RESULT=" + repr(r), flush=True)
except KeyboardInterrupt:
    print("[child] KEYBOARD_INTERRUPT", flush=True)
except EOFError:
    print("[child] EOF", flush=True)
'''

def run_case(keys, label, tail_sleep=0.8):
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp("python3", ["python3", "-c", child_code])
    time.sleep(1.2)
    for k in keys:
        os.write(fd, k)
        time.sleep(0.3)
    time.sleep(tail_sleep)
    out = b""
    while True:
        r, _, _ = select.select([fd], [], [], 0.3)
        if not r:
            break
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        out += chunk
    print(f"=== {label} ===")
    print(out.decode(errors="replace"))
    try:
        os.close(fd)
    except Exception:
        pass

# 场景 1: Alt+Enter（\x1b\r，ICRNL 开启时终端会转成 escape+c-j）→ 应换行，再 Enter 提交多行
run_case([b"line1", b"\x1b\r", b"line2", b"\r"], "Alt+Enter(\\r) + Enter submit")

# 场景 2: Ctrl+C 清空输入（\x03）→ CANCEL，再输入并 Enter 提交
run_case([b"junk", b"\x03", b"ok", b"\r"], "Ctrl+C clear then submit")

# 场景 3: 单独 ESC（\x1b，meta 超时）→ ESC_EXIT + EOF（REPL 应退出）
run_case([b"abc", b"\x1b"], "ESC alone -> EOF exit", tail_sleep=1.5)
