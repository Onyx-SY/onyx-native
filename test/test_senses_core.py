# -*- coding: utf-8 -*-
"""感官系统（senses）核心自校验：采样 / 防抖 / 自编辑抑制 / 关键词聚焦 / 静默提醒。

运行：python3 test/test_senses_core.py   （退出码 0 = 通过）
"""
import os
import shutil
import sys
import tempfile
import time

HOME = tempfile.mkdtemp(prefix="senses_home_")
os.environ["HOME"] = HOME
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bin.ai_lib import senses  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print("  ✅ %s" % name)
    else:
        print("  ❌ %s %s" % (name, detail))
        FAILS.append(name)


def main():
    work = tempfile.mkdtemp(prefix="senses_work_")
    senses.set_workspace(work)          # 注意力按工作区隔离：本测试绑定到临时目录
    target = os.path.join(work, "watched.py")
    with open(target, "w", encoding="utf-8") as f:
        f.write("line1\nline2\nMEM_KEY\nline4\nline5\n")

    m = senses._mgr()
    cfg = m.cfg
    cfg["enabled"] = True
    cfg["user_approved"] = True
    cfg["notify"]["debounce_sec"] = 0
    cfg["notify"]["min_interval_sec"] = 0
    cfg["attentions"] = []
    m._save()

    print("== 1. 注册注意力 ==")
    r = senses.add_file(target, 1, 0, trigger="on_change", debounce_sec=0)
    check("add_file ok", r.get("ok"), r)
    aid = r["attention"]["id"]
    m._tick()
    snap = m._snap.get(aid) or {}
    check("首帧 STATE=OK", snap.get("state") == "ok", snap.get("state"))
    check("首帧不产生事件", len(senses.pop_events()) == 0)

    print("== 2. 外部改动 → 防抖后投递事件（含改前/改后）==")
    time.sleep(0.02)
    with open(target, "a", encoding="utf-8") as f:
        f.write("line6-外部新增\n")
    m._tick()
    evs = senses.pop_events()
    check("外部改动产生 1 个事件", len(evs) == 1, len(evs))
    if evs:
        ev = evs[0]
        check("事件带改前内容", "line5" in (ev.get("before") or ""), ev.get("before"))
        check("事件带改后内容", "line6-外部新增" in (ev.get("after") or ""), ev.get("after"))
        check("事件标记 external", ev.get("state") == "external", ev.get("state"))

    print("== 3. AI 自己改（note_self_edit）→ 视觉更新但不投递 ==")
    senses.note_self_edit(target)
    time.sleep(0.02)
    with open(target, "a", encoding="utf-8") as f:
        f.write("line7-AI自己改\n")
    m._tick()
    evs = senses.pop_events()
    check("自编辑不投递事件", len(evs) == 0, len(evs))
    check("视觉仍更新为 SELF", (m._snap.get(aid) or {}).get("state") == "self",
          (m._snap.get(aid) or {}).get("state"))

    print("== 4. 关键词聚焦 ±N 行 ==")
    senses.clear_attentions()
    r = senses.add_file(target, keyword="MEM_KEY", context=1, unit="lines",
                        trigger="on_change", debounce_sec=0)
    check("add_file(focus) ok", r.get("ok"), r)
    kid = r["attention"]["id"]
    m._tick()
    text = (m._snap.get(kid) or {}).get("text") or ""
    check("聚焦窗口含关键词", "MEM_KEY" in text, repr(text))
    check("聚焦窗口是 ±1 行", text.count("\n") <= 2, repr(text))

    print("== 5. 关键词消失 → NO-KEYWORD 事件 ==")
    with open(target, "w", encoding="utf-8") as f:
        f.write("nothing here\n")
    m._tick()
    evs = senses.pop_events()
    check("关键词消失产生事件", len(evs) >= 1, len(evs))
    check("状态 focus_missing",
          (m._snap.get(kid) or {}).get("state") == "focus_missing",
          (m._snap.get(kid) or {}).get("state"))

    print("== 6. 静默提醒（长时间无变化）==")
    senses.clear_attentions()
    r = senses.add_file(target, 1, 0, trigger="on_change", debounce_sec=0, silent_sec=0.05)
    sid = r["attention"]["id"]
    m._tick()
    senses.pop_events()
    time.sleep(0.12)
    m._tick()
    evs = senses.pop_events()
    check("静默超时提醒一次", any(e.get("state") == "silent" for e in evs), evs)
    m._tick()
    check("静默只提醒一次", len(senses.pop_events()) == 0)

    print("== 7. 视觉文档格式 ==")
    body = m._render(write=True)
    check("含标题", body.startswith("# 视觉"), body[:40])
    check("含 [Vn] 标记", "[V" in body)
    check("含范围描述", ": L" in body or "focus=" in body)
    check("内容用 <<< >>> 包裹", "<<<" in body and ">>>" in body)
    vp = senses._vision_path()
    check("vision.md 已落盘（工作区内）", os.path.exists(vp), vp)
    check("vision.md 头部含 Workspace", "Workspace:" in open(vp, encoding="utf-8").read()[:400])

    print("== 8. 上限与清空 ==")
    senses.clear_attentions()
    cfg = m.cfg
    cfg["vision"]["max_attentions"] = 2
    m._save()
    a1 = senses.add_file(target, 1, 2)
    a2 = senses.add_file(target, 1, 2)
    a3 = senses.add_file(target, 1, 2)
    check("前两个可加", a1.get("ok") and a2.get("ok"), (a1, a2))
    check("超过上限被拒", not a3.get("ok"), a3)
    check("clear 生效", senses.clear_attentions().get("ok"))

    print("== 9. eyes 工具入口 ==")
    out = senses.handle_eyes_action(action="list")
    check("eyes list 可调用", isinstance(out, str) and "注意力" in out, out[:60])
    out = senses.handle_eyes_action(action="status")
    check("eyes status 可调用", "主动感知" in out, out[:60])

    print("== 10. 安全：敏感路径 / 二进制 / 控制字符 ==")
    ssh = os.path.join(work, "id_rsa")
    with open(ssh, "w", encoding="utf-8") as f:
        f.write("PRIVATE KEY\n")
    r = senses.add_file(ssh, 1, 5)
    check("拒绝 SSH 私钥", (not r.get("ok")) and ("拒绝" in str(r.get("error"))), r)
    envf = os.path.join(work, ".env")
    with open(envf, "w", encoding="utf-8") as f:
        f.write("API_KEY=xxx\n")
    check("拒绝 .env", not senses.add_file(envf, 1, 5).get("ok"))
    keyf = os.path.join(work, "server.pem")
    with open(keyf, "w", encoding="utf-8") as f:
        f.write("x\n")
    check("拒绝 .pem", not senses.add_file(keyf, 1, 5).get("ok"))
    check("正常文件放行", senses.check_watchable(target) == "")
    binf = os.path.join(work, "blob.bin")
    with open(binf, "wb") as f:
        f.write(b"abc" + bytes([0]) + b"def")
    check("拒绝二进制", "二进制" in senses.check_watchable(binf))

    esc = chr(27)
    raw = esc + "[2Jclear" + esc + "]0;title" + chr(7) + "ok"
    check("剥 ESC/OSC 序列", senses._clean_text(raw) == "clearok",
          repr(senses._clean_text(raw)))
    check("保留换行制表", senses._clean_text("a\n\tb") == "a\n\tb")

    seg = ["L%d" % i for i in range(1000)]
    txt, trunc = senses._clip_lines(seg, 10)
    check("截断标记存在", bool(trunc) and ("省略" in txt), txt[:80])
    check("截断保留头尾", txt.startswith("L0") and txt.rstrip().endswith("L999"), txt[-40:])

    print("== 11. 性能：stat 快路径 ==")
    senses.clear_attentions()
    r = senses.add_file(target, 1, 0, trigger="on_change", debounce_sec=0)
    m._tick()
    n0 = m._read_count
    m._tick()
    m._tick()
    m._tick()
    check("未变化时不重读文件", m._read_count == n0, (n0, m._read_count))
    time.sleep(0.02)
    with open(target, "a", encoding="utf-8") as f:
        f.write("touch\n")
    m._tick()
    check("变化后重读一次", m._read_count == n0 + 1, (n0, m._read_count))
    senses.clear_attentions()

    shutil.rmtree(work, ignore_errors=True)
    shutil.rmtree(HOME, ignore_errors=True)
    print()
    if FAILS:
        print("FAILED: %s" % ", ".join(FAILS))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
