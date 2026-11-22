# -*- coding: utf-8 -*-
"""感官系统：注意力按工作目录隔离（工作区）自校验。

运行：python3 test/test_senses_workspace.py   （退出码 0 = 通过）
"""
import json
import os
import shutil
import sys
import tempfile

HOME = tempfile.mkdtemp(prefix="senses_ws_home_")
os.environ["HOME"] = HOME
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bin.ai_lib import senses  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print("  OK  %s" % name if cond else "  FAIL %s %s" % (name, detail))
    if not cond:
        FAILS.append(name)


def main():
    root = tempfile.mkdtemp(prefix="senses_ws_work_")
    A = os.path.join(root, "projA")
    B = os.path.join(root, "projB")
    C = os.path.join(root, "projC")
    for d in (A, B, C):
        os.makedirs(d, exist_ok=True)
    with open(os.path.join(A, "a.log"), "w", encoding="utf-8") as f:
        f.write("A line1\n")
    with open(os.path.join(B, "b.log"), "w", encoding="utf-8") as f:
        f.write("B line1\n")

    print("== 1. 绑定工作区 A，相对路径按工作区根解析 ==")
    senses.set_workspace(A)
    check("current_workspace == A", senses.current_workspace() == os.path.realpath(A),
          senses.current_workspace())
    m = senses._mgr()
    cfg = m.cfg
    cfg["enabled"] = True
    cfg["user_approved"] = True
    cfg["notify"]["debounce_sec"] = 0
    cfg["attentions"] = []
    m._save()
    r = senses.add_file("a.log", 1, 0, trigger="on_change", debounce_sec=0)
    check("相对路径 add_file 成功", r.get("ok"), r)
    att = r.get("attention") or {}
    check("展示为相对路径", att.get("path") == "a.log", att.get("path"))
    m._tick()
    vis_a = senses.get_vision_block()
    check("视觉含 a.log", "a.log" in vis_a, vis_a[:120])
    path_a = senses._vision_path()
    body_a = open(path_a, encoding="utf-8").read()
    check("vision.md 在 workspaces 下", "workspaces" in path_a, path_a)
    check("vision.md 头含 Workspace: A", ("Workspace: " + os.path.realpath(A)) in body_a,
          body_a[:200])

    print("== 2. 切到 B：注意力集独立、视觉不串 ==")
    switched = senses.sync_workspace(B)
    check("检测到目录切换", switched is True)
    check("B 的注意力为空", len(senses._mgr().cfg.get("attentions") or []) == 0,
          senses._mgr().cfg.get("attentions"))
    vis_b0 = senses.get_vision_block()
    check("B 视觉不含 A 的文件", "a.log" not in vis_b0, vis_b0[:120])
    senses.add_file("b.log", 1, 0, trigger="on_change", debounce_sec=0)
    senses._mgr()._tick()
    path_b = senses._vision_path()
    body_b = open(path_b, encoding="utf-8").read()
    check("A/B 视觉文件不同", path_a != path_b)
    check("B 视觉含 b.log", "b.log" in body_b, body_b[:160])
    check("B 视觉不含 a.log", "a.log" not in body_b, body_b[:160])

    print("== 3. 切回 A：注意力持久化恢复 ==")
    senses.sync_workspace(A)
    atts = senses._mgr().cfg.get("attentions") or []
    check("A 的注意力还在", any(x.get("path") == "a.log" for x in atts), atts)
    check("A 视觉恢复", "a.log" in open(senses._vision_path(), encoding="utf-8").read())

    print("== 4. 无变化时不切换（零开销）==")
    check("同目录 sync 返回 False", senses.sync_workspace(A) is False)

    print("== 5. workspaces 列表 ==")
    items = senses.list_workspaces()
    check("至少 2 个工作区", len(items) >= 2, len(items))
    check("恰好一个 current", sum(1 for it in items if it.get("current")) == 1, items)
    check("A 工作区注意力数 >= 1",
          any(it.get("path") == os.path.realpath(A) and int(it.get("attentions") or 0) >= 1
              for it in items), items)
    out = senses.handle_eyes_action(action="workspaces")
    check("eyes workspaces 可调用", "工作区" in out, out[:120])
    check("status 显示工作区", "工作区" in senses.status_text())

    print("== 6. 旧全局注意力迁移（只迁一次）==")
    shutil.rmtree(senses._workspaces_root(), ignore_errors=True)
    tpl = senses._template_path()
    tdata = {}
    try:
        tdata = json.load(open(tpl, encoding="utf-8"))
    except Exception:
        tdata = {}
    tdata["attentions"] = [{"id": 1, "type": "file", "path": "old.log", "start": 1,
                            "end": 0, "enabled": True, "trigger": "on_change"}]
    with open(tpl, "w", encoding="utf-8") as f:
        json.dump(tdata, f, ensure_ascii=False)
    senses._WS.clear()
    senses._reset_manager()
    senses.set_workspace(C)
    cfg_c = json.load(open(senses._config_path(), encoding="utf-8"))
    check("旧注意力已迁入首个工作区", len(cfg_c.get("attentions") or []) == 1,
          cfg_c.get("attentions"))
    tpl_after = json.load(open(tpl, encoding="utf-8"))
    check("模板里的注意力已清空", not (tpl_after.get("attentions") or []),
          tpl_after.get("attentions"))
    # 第二个工作区不再迁移
    senses.set_workspace(A)
    cfg_a2 = json.load(open(senses._config_path(), encoding="utf-8"))
    check("新工作区不会复制旧注意力", not (cfg_a2.get("attentions") or []),
          cfg_a2.get("attentions"))

    shutil.rmtree(root, ignore_errors=True)
    shutil.rmtree(HOME, ignore_errors=True)
    print()
    if FAILS:
        print("FAILED: %s" % ", ".join(FAILS))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
