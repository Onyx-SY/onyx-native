# -*- coding: utf-8 -*-
"""step-4 验证：历史格式/迁移/去重 + 非破坏性前缀导航 + 模糊排序。"""
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
fails = []

from lib.terminal.repl.history import (OnyxHistory, fuzzy_score, fuzzy_rank,  # noqa: E402
                                       install_history_bindings)
from lib.terminal.repl import editor as E                                     # noqa: E402

ESC = "\x1b"
UP = ESC + "[A"
DOWN = ESC + "[B"


def tmpfile(name="hist"):
    d = tempfile.mkdtemp(prefix="onyx_hist_")
    return os.path.join(d, name)


# ── ① 字面 \n 必须原样保留（旧实现的损坏点）──────────────────────
p = tmpfile()
h = OnyxHistory(p)
h.store_string('printf "a\\nb"')          # 命令里是字面反斜杠+n
h.store_string("echo one\necho two")      # 真多行
h2 = OnyxHistory(p)
texts = h2.get_strings()
print("① 读回:", texts)
if 'printf "a\\nb"' not in texts:
    fails.append('字面 \\n 被破坏')
if "echo one\necho two" not in texts:
    fails.append("真多行未还原")
if len(texts) != 2:
    fails.append(f"条目数异常 {len(texts)}")

# ── ② 旧格式迁移：不做 \\n 猜测 ─────────────────────────────────
p2 = tmpfile("legacy")
with io.open(p2, "w", encoding="utf-8") as f:
    f.write('printf "a\\nb"\n')
    f.write('{"multiline": true, "cmd": "if true; then\\n  echo hi\\nfi"}\n')
    f.write("ls -la\n")
h3 = OnyxHistory(p2)
got = h3.get_strings()
print("② 迁移后:", got, "| migrated =", h3.migrated)
if not h3.migrated:
    fails.append("未触发迁移")
if 'printf "a\\nb"' not in got:
    fails.append('迁移把字面 \\n 拆坏了')
if "if true; then\n  echo hi\nfi" not in got:
    fails.append("JSON 多行块未解码")
# 迁移后再次加载应走新格式
h4 = OnyxHistory(p2)
if sorted(h4.get_strings()) != sorted(got):
    fails.append("迁移后二次加载不一致")

# ── ③ 去重 / 忽略空格 ──────────────────────────────────────────
p3 = tmpfile("dedup")
h5 = OnyxHistory(p3)
h5.store_string("ls"); h5.store_string("git status"); h5.store_string("ls")
h5.store_string("  secret")            # 空格开头 → 忽略
h6 = OnyxHistory(p3)
gs = h6.get_strings()
print("③ 去重后:", gs)
if gs.count("ls") != 1 or gs[-1] != "ls":
    fails.append("去重/最新优先不对")
if any("secret" in x for x in gs):
    fails.append("空格开头的命令被写入了")

# ── ④ 非破坏性前缀导航（pipe 驱动）──────────────────────────────
def run_nav(keys, history_entries):
    from prompt_toolkit import PromptSession
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    hp = tmpfile("nav")
    hist = OnyxHistory(hp)
    for s in history_entries:          # 顺序写入 → 最后一个是最新的
        hist.store_string(s)
    hist = OnyxHistory(hp)
    kb = E.build_key_bindings()
    install_history_bindings(kb, hist)
    flags = {k: v for k, v in E.SESSION_FLAGS.items() if k != "complete_while_typing"}
    with create_pipe_input() as inp:
        sess = PromptSession(input=inp, output=DummyOutput(), key_bindings=kb,
                             history=hist, **flags)
        inp.send_bytes(keys.encode("latin-1"))
        return sess.prompt()


hist_entries = ["git status", "git push origin main", "ls -la", "git commit -m x"]
r = run_nav("git" + UP + "\r", hist_entries)
print(f"④ ↑ 前缀匹配: {r!r}（期望 'git commit -m x'，即最新一条以 git 开头的）")
if r != "git commit -m x":
    fails.append(f"↑ 前缀匹配错误: {r!r}")

r2 = run_nav("git" + UP + UP + "\r", hist_entries)
print(f"④ ↑↑ 继续上溯: {r2!r}（期望 'git push origin main'）")
if r2 != "git push origin main":
    fails.append(f"↑↑ 错误: {r2!r}")

r3 = run_nav("git" + UP + DOWN + "\r", hist_entries)
print(f"④ ↑↓ 必须还原原输入: {r3!r}（期望 'git'）")
if r3 != "git":
    fails.append(f"↓ 未还原原输入（旧实现的硬伤）: {r3!r}")

r4 = run_nav("zzz" + UP + "\r", hist_entries)
print(f"④ 无匹配时不动: {r4!r}（期望 'zzz'）")
if r4 != "zzz":
    fails.append(f"无匹配被改动: {r4!r}")

# ── ⑤ 模糊排序 ─────────────────────────────────────────────────
class _E:
    def __init__(self, t, ts=0.0):
        self.text, self.ts, self.cwd = t, ts, ""


cands = [_E("git commit -m 'fix bug'"), _E("git checkout main"),
         _E("grep -rn TODO ."), _E("ls -la")]
ranked = [e.text for e in fuzzy_rank("gcm", cands)]
print("⑤ fuzzy('gcm') →", ranked)
if not ranked or "git commit -m 'fix bug'" != ranked[0]:
    fails.append(f"模糊排序首位不对: {ranked[:2]}")
print("⑤ fuzzy_score 不匹配返回 None:", fuzzy_score("zzz", "git status") is None)
if fuzzy_score("zzz", "git status") is not None:
    fails.append("模糊匹配误命中")

print("\nFAILS:", fails if fails else "none")
sys.exit(1 if fails else 0)
