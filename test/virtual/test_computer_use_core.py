#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""computer_use 核心单测（离线，不触真实桌面）。

覆盖：
  1. 配置读写与默认值合并（临时 HOME 隔离）
  2. 平台探测与后端选择
  3. Android launch 命令生成（包名 / URL）
  4. X11 click / key / scroll 命令生成（mock runner 断言 argv）
  5. 控件索引越界、缺参数、未知 action 等错误路径
  6. safe_mode 高危按键拦截
  7. find 控件匹配、_cap 截断、PNG 尺寸解析

运行: python3 test/virtual/test_computer_use_core.py
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

# 隔离 HOME，避免污染真实 ~/.ai_s/computer_use
_TMP_HOME = tempfile.mkdtemp(prefix="onyx_cu_home_")
os.environ["HOME"] = _TMP_HOME

from bin.ai_lib import computer_use as cu  # noqa: E402

_FAILED = []


def check(name, cond, detail=""):
    if cond:
        print("  PASS %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        _FAILED.append(name)


def make_runner(record, rc=0, out="", err=""):
    """构造记录 argv 的假 runner。"""
    def runner(argv, timeout, input_text=None):
        record.append(list(argv))
        return rc, out, err
    return runner


class FakeWhich:
    """临时替换 cu._which：所有命令都「存在」。"""

    def __init__(self, names=None):
        self.names = names  # None = 全部存在

    def __enter__(self):
        self._orig = cu._which
        names = self.names

        def _fake(name):
            if names is None or name in names:
                return "/usr/bin/" + name
            return None
        cu._which = _fake
        return self

    def __exit__(self, *exc):
        cu._which = self._orig
        return False


# ══════════════════════════════════════════════════════════
# 1. 配置
# ══════════════════════════════════════════════════════════

def test_config():
    print("[配置]")
    cfg = cu.load_config()
    check("默认配置含 backend", cfg.get("backend") == "auto", cfg.get("backend"))
    check("默认 max_tree_nodes=300", cfg.get("max_tree_nodes") == 300)
    check("配置路径在 ~/.ai_s/computer_use",
          cu.config_path().endswith(os.path.join(".ai_s", "computer_use", "config.json")),
          cu.config_path())

    cfg["backend"] = "x11"
    cfg["timeout"] = 7
    cu.save_config(cfg)
    reloaded = cu.load_config()
    check("配置持久化 backend", reloaded.get("backend") == "x11")
    check("配置持久化 timeout", reloaded.get("timeout") == 7)

    # 损坏配置 → 回退默认值
    with open(cu.config_path(), "w", encoding="utf-8") as f:
        f.write("{ this is not json")
    broken = cu.load_config()
    check("损坏配置回退默认", broken.get("backend") == "auto", broken.get("backend"))
    cu.save_config(cu.DEFAULT_CONFIG)

    check("截图目录默认在 shots/",
          cu.shots_dir(cu.DEFAULT_CONFIG).endswith("shots"), cu.shots_dir(cu.DEFAULT_CONFIG))


# ══════════════════════════════════════════════════════════
# 2. 平台与后端
# ══════════════════════════════════════════════════════════

def test_platform():
    print("[平台探测]")
    plat = cu.detect_platform()
    check("detect_platform 返回已知平台",
          plat in ("windows", "android", "wayland", "x11", "unknown", "macos"), plat)

    backend = cu.get_backend(cu.DEFAULT_CONFIG, "android")
    check("显式指定 android 后端", backend.name == "android", backend.name)
    check("android 支持 launch", backend.supports("launch"))
    check("android 不支持 click", not backend.supports("click"))

    null_backend = cu.get_backend(cu.DEFAULT_CONFIG, "null")
    check("null 后端仅支持 capability", null_backend.supports("capability")
          and not null_backend.supports("launch"))

    text = cu.render_capability(backend)
    check("capability 文本含 backend 行", "backend: android" in text, text[:80])
    check("capability 文本含 actions 行", "actions:" in text)


# ══════════════════════════════════════════════════════════
# 3. Android launch
# ══════════════════════════════════════════════════════════

def test_android_launch():
    print("[Android launch]")
    rec = []
    with FakeWhich():
        be = cu.AndroidBackend(dict(cu.DEFAULT_CONFIG), runner=make_runner(rec))
        out = be.launch(app="com.tencent.mm")
        check("包名 launch 返回 ok", out.startswith("launch ok"), out)
        first = rec[0]
        check("包名 launch 用 am start", first[0] == "am" and first[1] == "start", first)
        check("包名 launch 带 LAUNCHER 类别",
              "android.intent.category.LAUNCHER" in first, first)
        check("包名 launch 带 -p 包名", "-p" in first and "com.tencent.mm" in first, first)

        rec.clear()
        out = be.launch(app="https://example.com")
        check("URL launch 返回 ok", out.startswith("launch ok"), out)
        check("URL launch 用 VIEW + -d",
              rec[0][:3] == ["am", "start", "-a"]
              and "android.intent.action.VIEW" in rec[0]
              and "-d" in rec[0], rec[0])

        rec.clear()
        out = be.launch(app="")
        check("空 app 报错", out.startswith("error:"), out)

        # am 报 Error type 3 → 判定失败
        be2 = cu.AndroidBackend(dict(cu.DEFAULT_CONFIG),
                                runner=make_runner([], rc=0,
                                                   out="Error type 3\nError: Activity class does not exist."))
        out = be2.launch(app="com.nope.app")
        check("Activity 不存在时返回 error", out.startswith("error:"), out[:120])


# ══════════════════════════════════════════════════════════
# 4. X11 命令生成
# ══════════════════════════════════════════════════════════

def test_x11_actions():
    print("[X11 命令生成]")
    cfg = dict(cu.DEFAULT_CONFIG)
    cfg["backend"] = "x11"

    with FakeWhich():
        rec = []
        be = cu.X11Backend(cfg, runner=make_runner(rec))

        out = be.click(x=10, y=20)
        check("click 返回 ok", out.startswith("click ok"), out)
        check("click 生成 mousemove+click",
              rec[0][:3] == ["xdotool", "mousemove", "10"]
              and "click" in rec[0] and "20" in rec[0], rec[0])

        rec.clear()
        out = be.click(control=0)
        check("无节点缓存时 control 报错", out.startswith("error:"), out)

        # 有节点缓存 → 按索引取中心点
        be._nodes = [{"i": 0, "role": "push button", "name": "OK",
                      "rect": [100, 200, 40, 20]}]
        rec.clear()
        out = be.click(control=0)
        check("control 索引点击取中心", out.startswith("click ok") and "120" in rec[0]
              and "210" in rec[0], (out, rec))

        rec.clear()
        out = be.click(control=99)
        check("control 越界报错", out.startswith("error:") and "越界" in out, out)

        rec.clear()
        out = be.type_text(text="hello")
        check("type 返回 ok", out.startswith("type ok"), out)
        check("type 用 xdotool type", rec[0][:2] == ["xdotool", "type"], rec[0])
        check("type 用 -- 保护文本", "--" in rec[0] and "hello" in rec[0], rec[0])

        rec.clear()
        out = be.key(key="ctrl+s")
        check("key 返回 ok", out.startswith("key ok"), out)
        check("key 传组合键", "ctrl+s" in rec[0], rec[0])

        out = be.key(key="ctrl+alt+delete")
        check("safe_mode 拦截高危键", out.startswith("error:") and "safe_mode" in out, out)

        rec.clear()
        out = be.scroll(direction="down", amount=5)
        check("scroll 返回 ok", out.startswith("scroll ok"), out)
        check("scroll down 用按钮 5", "5" in rec[0], rec[0])

        out = be.scroll(direction="diagonal")
        check("非法方向报错", out.startswith("error:"), out)

        rec.clear()
        out = be.drag(x=1, y=2, to_x=30, to_y=40)
        check("drag 返回 ok", out.startswith("drag ok"), out)
        check("drag 含 mousedown/mouseup",
              "mousedown" in rec[0] and "mouseup" in rec[0], rec[0])

        out = be.drag(x=None, y=None, to_x=None, to_y=None)
        check("drag 缺参数报错", out.startswith("error:"), out)


def test_x11_find():
    print("[X11 find]")
    cfg = dict(cu.DEFAULT_CONFIG)
    be = cu.X11Backend(cfg, runner=make_runner([]))
    be._nodes = [
        {"i": 0, "role": "push button", "name": "Save", "rect": [0, 0, 10, 10]},
        {"i": 1, "role": "push button", "name": "Cancel", "rect": [0, 0, 10, 10]},
        {"i": 2, "role": "entry", "name": "search box", "rect": [0, 0, 10, 10]},
    ]
    out = be.find(pattern="save")
    check("find 按名匹配（忽略大小写）", "Save" in out and "Cancel" not in out, out)
    check("find 返回节点索引", "[0]" in out, out)

    out = be.find(role="entry")
    check("find 按角色过滤", "search box" in out and "Save" not in out, out)

    out = be.find(pattern="zzz")
    check("find 无匹配", "0 个匹配" in out, out)

    out = be.find()
    check("find 缺参数报错", out.startswith("error:"), out)

    out = be.find(pattern="[")
    check("find 非法正则报错", out.startswith("error:") and "正则" in out, out)


# ══════════════════════════════════════════════════════════
# 5. 工具入口错误路径
# ══════════════════════════════════════════════════════════

def test_entry_errors():
    print("[入口错误路径]")
    out = cu.handle_computer_use()
    check("缺 action 报错", out.startswith("error:") and "action" in out, out)

    out = cu.handle_computer_use(action="fly")
    check("未知 action 报错", out.startswith("error:") and "未知" in out, out)

    out = cu.handle_computer_use(action="click", backend="null")
    check("null 后端不支持 click", out.startswith("error:") and "不支持" in out, out)

    out = cu.handle_computer_use(action="capability", backend="null")
    check("null 后端 capability 可用", out.startswith("capability ok"), out[:60])


# ══════════════════════════════════════════════════════════
# 6. 工具函数
# ══════════════════════════════════════════════════════════

def test_helpers():
    print("[工具函数]")
    short = "abc"
    check("_cap 不截断短文本", cu._cap(short, 100) == short)

    long_text = "x" * 500
    capped = cu._cap(long_text, 100)
    check("_cap 截断长文本", len(capped) < 500 and "truncated" in capped, len(capped))
    check("_cap 保留头尾", capped.startswith("xxx") and capped.endswith("xxx"))

    # 构造最小 PNG 头：800x600
    png = (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR"
           + (800).to_bytes(4, "big") + (600).to_bytes(4, "big")
           + b"\x08\x02\x00\x00\x00")
    p = os.path.join(_TMP_HOME, "fake.png")
    with open(p, "wb") as f:
        f.write(png)
    check("_png_size 解析宽高", cu._png_size(p) == (800, 600), cu._png_size(p))
    check("_png_size 非 PNG 返回 0", cu._png_size(__file__) == (0, 0))

    check("_sha1_file 稳定", cu._sha1_file(p) == cu._sha1_file(p) and len(cu._sha1_file(p)) == 40)

    # 节点缓存读写
    nodes = [{"i": 0, "role": "push button", "name": "A", "rect": [1, 2, 3, 4]}]
    cu.save_nodes(nodes)
    check("节点缓存落盘并可读回", cu.load_nodes() == nodes, cu.load_nodes())


# ══════════════════════════════════════════════════════════
# main
# ══════════════════════════════════════════════════════════

def main():
    test_config()
    test_platform()
    test_android_launch()
    test_x11_actions()
    test_x11_find()
    test_entry_errors()
    test_helpers()
    print("")
    if _FAILED:
        print("FAILED %d: %s" % (len(_FAILED), ", ".join(_FAILED)))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
