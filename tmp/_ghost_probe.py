"""探测：SmartCompleter 的 history_buffer 是否与 _HISTORY_BUFFER 共享（当前会话命令能否进入虚影）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.terminal import input_lib as il
from lib.terminal.com import SmartCompleter

# 模拟启动时已从历史文件加载（旧会话命令）
il._HISTORY_INITIALIZED = True
il._HISTORY_BUFFER = ["git status", "ls -la"]

c = SmartCompleter(["ls", "git", "echo"], history_buffer=il._HISTORY_BUFFER, user_home_dir="")
print("共享同一列表对象? ", c.history_buffer is il._HISTORY_BUFFER)
print("创建时 completer.history_buffer =", c.history_buffer)

# 模拟当前会话新输入一条命令
il.add_to_history("echo hello-world")

print("add 之后 _HISTORY_BUFFER =", il._HISTORY_BUFFER)
print("add 之后 completer.history_buffer =", c.history_buffer)
print("共享同一列表对象? ", c.history_buffer is il._HISTORY_BUFFER)
print("get_smart_suggestion('echo') =", repr(c.get_smart_suggestion("echo")))
print("get_smart_suggestion('git')  =", repr(c.get_smart_suggestion("git")))

# 空历史场景：验证 `history_buffer or []` 是否会丢弃引用
il2 = ["x"]
c2 = SmartCompleter(["ls"], history_buffer=[], user_home_dir="")
print("空历史传入时 completer.history_buffer is 原对象? ", c2.history_buffer is il2)
