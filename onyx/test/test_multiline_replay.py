#!/usr/bin/env python3
"""多行命令简化行为测试：
- 整块回显 + 原样执行（无续行提示符、无逐行状态机）
- heredoc Ctrl+D/EOF 自动补结束符（不再触发二次 📥 提示）
- 虚影补全跳过含换行的历史命令（不再出现 ^J）
- 右键绑定：多行虚影 → 待重放 + 清空缓冲区
"""
import io
import os
import sys
import contextlib

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from lib.terminal import input_lib
from lib.terminal.kb import create_key_bindings

def check(name, got, expected):
    status = "✅" if got == expected else "❌"
    if got != expected:
        print(f"{status} {name}\n      got:      {got!r}\n      expected: {expected!r}")
        return False
    print(f"{status} {name}")
    return True

ok = True

# ── 1. 整块回显：打印完整多行命令，原样返回 ──
raw = "cat > a.txt << EOF\nls\ncd solar-system/\nls\nEOF"
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    result = input_lib._replay_multiline_command(raw)
ok &= check("heredoc 整块回显并原样返回", result, raw)
ok &= check("回显打印完整多行块（无提示符）", buf.getvalue() == raw + "\n", True)

raw_if = "if [ -f x ]; then\n    echo y\nfi"
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    result = input_lib._replay_multiline_command(raw_if)
ok &= check("if/fi 整块回显", result, raw_if)
ok &= check("if/fi 回显无续行提示符", "if> " not in buf.getvalue(), True)

ok &= check("无结构多行原样返回", input_lib._replay_multiline_command("echo a\necho b"), "echo a\necho b")
ok &= check("单行命令原样返回", input_lib._replay_multiline_command("ls -la"), "ls -la")
ok &= check("残缺 heredoc 也原样返回（交给执行器）", input_lib._replay_multiline_command("cat <<EOF\nhello"), "cat <<EOF\nhello")

# ── 2. heredoc EOF 自动补结束符（回归：📥 二次提示）──
_orig_prompt = input_lib.prompt

# 2a. prompt 返回 None（ml_input.kb 的 Ctrl+D 路径）
input_lib.prompt = lambda *a, **k: None
result = input_lib._process_multiline_input("cat > a.txt << EOF")
ok &= check("Ctrl+D(None) → 自动补 EOF", result, "cat > a.txt << EOF\nEOF")
ok &= check("多行状态已复位", input_lib._MULTILINE_ACTIVE, False)

# 2b. prompt 抛 EOFError（外层 kb 的 Ctrl+D 路径）
def _raise_eof(*a, **k):
    raise EOFError()
input_lib.prompt = _raise_eof
result = input_lib._process_multiline_input("cat > a.txt << EOF")
ok &= check("Ctrl+D(EOFError) → 自动补 EOF", result, "cat > a.txt << EOF\nEOF")
ok &= check("多行状态已复位(EOFError)", input_lib._MULTILINE_ACTIVE, False)

# 2c. 非 heredoc 的 EOF：保持原行为（原样返回已输入内容）
result = input_lib._process_multiline_input("if x; then")
ok &= check("非 heredoc EOF 原样返回", result, "if x; then")

input_lib.prompt = _orig_prompt

# ── 3. 虚影补全跳过含换行的历史命令 ──
from lib.terminal.com import SmartCompleter
sc = SmartCompleter(
    ["cat", "ls"],
    show_hidden=True,
    cmd_config_path="",
    com_cmd_config_path="",
    virtual_root="",
    user_home_dir="",
    history_buffer=["cat > a.txt << EOF\nls\nEOF", "cat file", "ls -la"],
)
ok &= check("完整命令虚影跳过多行", sc._get_most_recent_full_command("cat"), "cat file")
ok &= check("get_smart_suggestion 不返回多行剩余", sc.get_smart_suggestion("cat"), " file")
ok &= check("命令名虚影跳过多行", sc._get_most_recent_command("cat"), "cat")
ok &= check("子命令虚影跳过多行", sc._get_most_recent_subcommand("cat", ["file", "x"]), "file")

# ── 4. 待重放消费逻辑 ──
input_lib._PENDING_MULTILINE_RECALL = None
ok &= check("无待重放时原样返回", input_lib._consume_pending_multiline_recall("ls"), "ls")

input_lib._PENDING_MULTILINE_RECALL = "cat > a.txt << EOF\nls\nEOF"
result = input_lib._consume_pending_multiline_recall("")
ok &= check("空输入 → 返回完整多行命令", result, "cat > a.txt << EOF\nls\nEOF")
ok &= check("消费后待重放被清空", input_lib._PENDING_MULTILINE_RECALL, None)

input_lib._PENDING_MULTILINE_RECALL = "cat > a.txt << EOF\nls\nEOF"
result = input_lib._consume_pending_multiline_recall("echo new")
ok &= check("用户已输入 → 放弃重放用新输入", result, "echo new")

# ── 5. kb 右键绑定 ──
class FakeBuffer:
    def __init__(self, text, suggestion):
        self.text = text
        self.cursor_position = len(text)
        self.suggestion = suggestion

    def insert_text(self, text):
        self.text = self.text[:self.cursor_position] + text + self.text[self.cursor_position:]
        self.cursor_position += len(text)

class FakeApp:
    def __init__(self, buffer):
        self.current_buffer = buffer

class FakeEvent:
    def __init__(self, buffer):
        self.app = FakeApp(buffer)

kb = create_key_bindings()
right_binding = None
for b in kb.bindings:
    if b.keys == ('right',):
        right_binding = b.handler
        break
assert right_binding is not None, "找不到 right 键绑定"

input_lib._PENDING_MULTILINE_RECALL = None
buf_multi = FakeBuffer("cat > a.txt << EOF", type("S", (), {"text": "\nls\nEOF"})())
right_binding(FakeEvent(buf_multi))
ok &= check("多行虚影 → 待重放已记录", input_lib._PENDING_MULTILINE_RECALL,
            "cat > a.txt << EOF\nls\nEOF")
ok &= check("多行虚影 → 缓冲区被清空", buf_multi.text, "")

input_lib._PENDING_MULTILINE_RECALL = None
buf_single = FakeBuffer("ls", type("S", (), {"text": " -la"})())
right_binding(FakeEvent(buf_single))
ok &= check("单行虚影正常插入", buf_single.text, "ls -la")
ok &= check("单行虚影不触发待重放", input_lib._PENDING_MULTILINE_RECALL, None)

print()
if not ok:
    sys.exit(1)
print("全部通过 ✅")
