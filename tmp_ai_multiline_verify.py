"""临时验证：AI 对话多行模式（Alt+Enter 切换）端到端 PTY 测试"""
import os, pty, sys, time, select

child_code = r'''
import os, sys
sys.path.insert(0, ".")
import bin.ai_interactive as ai

received = []

def fake_call(question, user_home_dir=None, onyx_module=None, global_config=None,
              user_info=None, user_mode=None, parse_and_execute=None, ctx=None, **kw):
    received.append(question)
    print(f"[child] AI_RECEIVED: {question!r}", flush=True)

ai._check_and_setup_key = lambda *a, **k: "fake-key-32chars-xxxxxxxxxxxxx"
ai._call_ai_engine = fake_call

class FakeOnyx:
    pass

ai.ai_interactive_session(
    user_home_dir=os.path.expanduser("~"),
    onyx_module=FakeOnyx(),
    global_config=None,
    user_info={"name": "tester"},
    user_mode=None,
    parse_and_execute=None,
)
print("[child] EXITED", flush=True)
'''

pid, fd = pty.fork()
if pid == 0:
    os.execvp("python3", ["python3", "-c", child_code])

time.sleep(2.5)  # 等待 REPL 启动

def send(data, delay=0.4):
    os.write(fd, data)
    time.sleep(delay)

# 普通模式输入 "hello"，然后 Alt+Enter（\x1b\r）→ 应进入多行模式
send(b"hello")
send(b"\x1b\r", 0.6)
# 多行模式：输入 "world" + Enter → 只换行暂存，不发送
send(b"world")
send(b"\r", 0.6)
# 多行模式：输入 "last" + Enter → 继续暂存
send(b"last")
send(b"\r", 0.6)
# Alt+Enter → 统一发送 hello\nworld\nlast 并退出多行模式
send(b"\x1b\r", 1.2)
# 普通模式：直接 Enter 发送一行 "solo"（验证模式已退出，Enter 恢复发送）
send(b"solo")
send(b"\r", 1.2)
# 退出
send(b"/exit\r", 1.2)

out = b""
while True:
    r, _, _ = select.select([fd], [], [], 1.0)
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

text = out.decode(errors="replace")
ok1 = "AI_RECEIVED: 'hello\\nworld\\nlast'" in text
ok2 = "AI_RECEIVED: 'solo'" in text
print("MULTILINE_OK:", ok1)
print("NORMAL_ENTER_OK:", ok2)
sys.exit(0 if (ok1 and ok2) else 1)
