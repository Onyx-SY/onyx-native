#!/usr/bin/env python3
"""多行命令历史 JSON 存储测试：编码/解码往返、旧格式兼容、普通行不受影响。"""
import os
import sys
import json

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from lib.terminal.input_lib import (
    _encode_multiline_for_storage,
    _decode_multiline_from_storage,
)

def check(name, got, expected):
    status = "✅" if got == expected else "❌"
    if got != expected:
        print(f"{status} {name}\n      got:      {got!r}\n      expected: {expected!r}")
        return False
    print(f"{status} {name}")
    return True

ok = True

# ── 1. 多行命令 → JSON 块，且文件行内无真实换行 ──
cmd = "if [ -f x ]; then\n    echo y\nfi"
enc = _encode_multiline_for_storage(cmd)
ok &= check("多行命令编码为 JSON 块", enc, '{"multiline": true, "cmd": "if [ -f x ]; then\\n    echo y\\nfi"}')
ok &= check("编码结果无真实换行（单行存储）", "\n" not in enc, True)
# 编码结果本身是合法 JSON
data = json.loads(enc)
ok &= check("编码结果是合法 JSON 且含标记", data.get("multiline") is True and data.get("cmd") == cmd, True)

# ── 2. 解码往返 ──
dec = _decode_multiline_from_storage(enc)
ok &= check("JSON 块解码还原多行命令", dec, cmd)

# ── 3. 单行命令原样存储 ──
plain = 'echo hello'
ok &= check("单行命令原样存储", _encode_multiline_for_storage(plain), plain)

# ── 4. 普通 { 开头命令不受影响 ──
brace = '{ echo hi; }'
ok &= check("普通 { 开头命令不误判", _decode_multiline_from_storage(brace), brace)

# ── 5. 无 multiline 标记的 JSON 不解码 ──
bare_json = '{"cmd": "ls"}'
ok &= check("无标记 JSON 不解码", _decode_multiline_from_storage(bare_json), bare_json)

# ── 6. 旧格式兼容：NUL 分隔符 / ^J / 字面 \\n ──
ok &= check("旧 NUL 格式兼容", _decode_multiline_from_storage("a\x00b"), "a\nb")
ok &= check("旧 ^J 格式兼容", _decode_multiline_from_storage("a^Jb"), "a\nb")
ok &= check("旧 \\n 字面格式兼容", _decode_multiline_from_storage("a\\nb"), "a\nb")

# ── 7. 中文内容往返 ──
cn_cmd = "echo 你好\nls"
ok &= check("中文多行命令往返", _decode_multiline_from_storage(_encode_multiline_for_storage(cn_cmd)), cn_cmd)

# ── 8. 空输入 ──
ok &= check("空输入", _decode_multiline_from_storage(""), "")

print()
if not ok:
    sys.exit(1)
print("全部通过 ✅")
