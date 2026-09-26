# -*- coding: utf-8 -*-
"""step-2 验证：配置零重复读盘 + PromptSession 会话级复用。"""
import builtins
import io
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
fails = []

from lib.terminal.repl import config as C
from lib.terminal.repl.session import ReplSession, session_key, BINDINGS_VERSION

# ── ① ConfigCache：签名不变时不重复读盘 ─────────────────────────
tmpdir = tempfile.mkdtemp(prefix="onyx_cfg_")
p = os.path.join(tmpdir, "ptk.json")
with io.open(p, "w", encoding="utf-8") as f:
    json.dump({"a": 1}, f)

real_open = builtins.open
counter = {"n": 0}


def counting_open(*a, **kw):
    if a and str(a[0]) == p:
        counter["n"] += 1
    return real_open(*a, **kw)


C.get_config_cache().clear()
builtins.open = counting_open
try:
    for _ in range(5):
        obj = C.get_config_cache().json(p, default={})
    after_first = counter["n"]
    print(f"① 读 5 次同签名文件 → 实际 open {after_first} 次（期望 1）")
    if after_first != 1:
        fails.append(f"重复读盘 {after_first} 次")
    if obj != {"a": 1}:
        fails.append("解析结果不对")

    # 改文件（内容长度也变）→ 签名变化 → 应重读
    with io.open(p, "w", encoding="utf-8") as f:
        json.dump({"a": 2, "b": "长一点的内容让 size 也变"}, f)
    obj2 = C.get_config_cache().json(p, default={})
    print(f"① 改文件后再读 → open 累计 {counter['n']} 次（期望 2），结果键 {sorted(obj2)}")
    if counter["n"] != 2 or "b" not in obj2:
        fails.append("签名变化后未重读")

    # 显式 invalidate → 必定重读（粗粒度文件系统上的兜底手段）
    C.get_config_cache().invalidate(p)
    C.get_config_cache().json(p, default={})
    print(f"① invalidate() 后 → open 累计 {counter['n']} 次（期望 3）")
    if counter["n"] != 3:
        fails.append("invalidate 未生效")
finally:
    builtins.open = real_open
print("   stats:", C.get_config_cache().stats())

# ── ② ReplSession：同签名复用，异签名重建 ───────────────────────
rs = ReplSession("test")
built = {"n": 0}


def builder():
    built["n"] += 1
    return f"session#{built['n']}"


k1 = session_key(commands_hash=hash(frozenset({"ls", "cd"})), sys_type="Linux",
                 terminal_type="bash", virtual_root="/root", user_home_dir="/root",
                 lang="chinese", config_sig=C.signature([p]))
for _ in range(10):
    s1 = rs.get(k1, builder)
print(f"② 同签名取 10 次 → builder 调用 {built['n']} 次（期望 1），会话 {s1!r}")
if built["n"] != 1:
    fails.append(f"同签名重复重建 {built['n']} 次")

k2 = session_key(commands_hash=hash(frozenset({"ls", "cd", "git"})), sys_type="Linux",
                 terminal_type="bash", virtual_root="/root", user_home_dir="/root",
                 lang="chinese", config_sig=C.signature([p]))
s2 = rs.get(k2, builder)
print(f"② 命令集合变化 → builder 调用 {built['n']} 次（期望 2），会话 {s2!r}")
if built["n"] != 2:
    fails.append("签名变化未重建")

# 键位版本必须参与签名（旧实现漏了 → 改键位不生效）
k3 = session_key(commands_hash=1, sys_type="Linux", terminal_type="bash",
                 virtual_root="", user_home_dir="", lang="chinese")
k4 = session_key(commands_hash=1, sys_type="Linux", terminal_type="bash",
                 virtual_root="", user_home_dir="", lang="chinese")
print(f"② 键位/主题版本已在签名内: {k3[-3:-1]} == ({BINDINGS_VERSION}, ...)")
if k3 != k4:
    fails.append("同参数签名不稳定")
if k3[-3] != BINDINGS_VERSION:
    fails.append("BINDINGS_VERSION 未进签名")
print("   session stats:", rs.stats())

print("\nFAILS:", fails if fails else "none")
sys.exit(1 if fails else 0)
