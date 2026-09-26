# -*- coding: utf-8 -*-
"""step-5 验证：目录级缓存（每按键零重复扫描）+ 模糊排序 + 转义 + ~ 展开。"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
fails = []

from lib.terminal.repl.complete import (DirCache, CommandIndex, shell_escape,   # noqa: E402
                                        expand_user, OnyxCompleter, rank_names)

# ── ① 目录级缓存：同目录连敲不重扫 ──────────────────────────────
d = tempfile.mkdtemp(prefix="onyx_comp_")
for i in range(300):
    open(os.path.join(d, "file_%03d.txt" % i), "w").close()
os.makedirs(os.path.join(d, "subdir"), exist_ok=True)
open(os.path.join(d, "with space.txt"), "w").close()

cache = DirCache(ttl=60)
import os as _os
_real_scandir = _os.scandir
count = {"n": 0}


def counting_scandir(*a, **kw):
    count["n"] += 1
    return _real_scandir(*a, **kw)


_os.scandir = counting_scandir
try:
    for pfx in "file_00 file_01 file_02 file_0 file_".split():
        cache.list_dir(d)
    print(f"① 同目录查询 5 次 → scandir {count['n']} 次（期望 1），hits={cache.hits}")
    if count["n"] != 1:
        fails.append(f"目录被重复扫描 {count['n']} 次")
finally:
    _os.scandir = _real_scandir

# ── ② 端到端：敲 10 个字符只扫一次目录 ─────────────────────────
comp = OnyxCompleter(commands=["git", "grep", "ls", "cd"], dir_cache=DirCache(ttl=60))
from prompt_toolkit.document import Document                      # noqa: E402
from prompt_toolkit.completion import CompleteEvent               # noqa: E402

scan2 = {"n": 0}


def counting_scandir2(*a, **kw):
    scan2["n"] += 1
    return _real_scandir(*a, **kw)


_os.scandir = counting_scandir2
try:
    results = []
    for n in range(3, 12):
        doc = Document("cat " + os.path.join(d, "file_0"[:n % 8 + 5]), len("cat ") + len(os.path.join(d, "file_0"[:n % 8 + 5])))
        results = list(comp.get_completions(doc, CompleteEvent()))
    print(f"② 连敲 9 次 → scandir {scan2['n']} 次（期望 1）")
    if scan2["n"] != 1:
        fails.append(f"按键触发了 {scan2['n']} 次目录扫描")
finally:
    _os.scandir = _real_scandir

# ── ③ 带空格路径必须转义 ───────────────────────────────────────
doc = Document("cat " + os.path.join(d, "with"), len("cat " + os.path.join(d, "with")))
outs = list(comp.get_completions(doc, CompleteEvent()))
texts = [c.text for c in outs]
print("③ 带空格路径补全:", texts)
if not any("with\\ space.txt" in t for t in texts):
    fails.append(f"空格未转义: {texts}")
print("③ shell_escape:", repr(shell_escape("a b/c$d.txt")))
if shell_escape("a b") != "a\\ b":
    fails.append("shell_escape 结果不对")

# ── ④ ~ 展开 ───────────────────────────────────────────────────
e1, s1 = expand_user("~/x")
e2, s2 = expand_user("~")
print(f"④ expand_user('~/x') → {e1!r} | ('~') → {e2!r}")
if not e1.endswith("/x") or e1.startswith("~"):
    fails.append("~ 未展开")
if e2 == "~":
    fails.append("单独的 ~ 未展开")

# ── ⑤ 命令索引 + 排序 ──────────────────────────────────────────
idx = CommandIndex(["git", "grep", "ls", "gcc", "python"])
cands = sorted(idx.candidates("g"))
print("⑤ 首字符索引 candidates('g') =", cands)
if set(cands) != {"git", "grep", "gcc"}:
    fails.append(f"索引结果不对: {cands}")

ranked = rank_names("gc", ["git", "grep", "gcc", "g++"],
                    freq={"gcc": 5}, recent={"git": 2})
print("⑤ rank('gc') =", [(n, round(s, 1)) for n, s, _ in ranked])
if ranked[0][0] not in ("gcc", "git"):
    fails.append(f"排序首位异常: {ranked[0][0]}")
names = [n for n, _, _ in ranked]
if "g++" in names[:2]:
    fails.append("前缀命中未优先于模糊命中")

# 前缀命中必须排在模糊命中之前
ranked2 = rank_names("ls", ["ls", "lsblk", "alias", "fls"])
order = [n for n, _, _ in ranked2]
print("⑤ rank('ls') =", order)
if order[0] != "ls" or order.index("ls") > order.index("fls"):
    fails.append(f"前缀优先失效: {order}")

# ── ⑥ 命令补全端到端 ───────────────────────────────────────────
doc = Document("gi", 2)
outs = [c.text for c in comp.get_completions(doc, CompleteEvent())]
print("⑥ 补全 'gi' →", outs)
if "git" not in outs:
    fails.append(f"命令补全漏了 git: {outs}")

print("\nFAILS:", fails if fails else "none")
sys.exit(1 if fails else 0)
