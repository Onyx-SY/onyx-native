# -*- coding: utf-8 -*-
"""Headless TUI 截图 → 文本网格（美化前后对比用）。
用法: python3 tmp/shot.py [cols] [rows] [out]
"""
import asyncio, os, sys, tempfile, time, uuid
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
COLS = int(sys.argv[1]) if len(sys.argv) > 1 else 120
ROWS = int(sys.argv[2]) if len(sys.argv) > 2 else 40
OUT = sys.argv[3] if len(sys.argv) > 3 else os.path.join(ROOT, "tmp", "shot.txt")
NS = "{http://www.w3.org/2000/svg}"


def _w(ch):
    o = ord(ch)
    if o >= 0x1100 and (o <= 0x115F or 0x2E80 <= o <= 0xA4CF or 0xAC00 <= o <= 0xD7A3
                        or 0xF900 <= o <= 0xFAFF or 0xFE30 <= o <= 0xFE6F
                        or 0xFF00 <= o <= 0xFF60 or 0xFFE0 <= o <= 0xFFE6
                        or 0x1F300 <= o <= 0x1FAFF or 0x2600 <= o <= 0x27BF):
        return 2
    return 1


def grid_of(svg, cols, rows):
    """按 Rich SVG 的等宽网格（12.2 x 24.4 px，首行基线 y=20）重建文本。"""
    g = [[" "] * cols for _ in range(rows)]
    try:
        root = ET.fromstring(svg)
    except Exception:
        return ["".join(r) for r in g]
    for t in root.iter(NS + "text"):
        try:
            if "title" in (t.get("class") or ""):   # SVG <title>，非屏幕内容
                continue
            x = float(t.get("x") or 0); y = float(t.get("y") or 0)
            txt = "".join(t.itertext())
        except Exception:
            continue
        col = int(round(x / 12.2)); row = int(round((y - 20) / 24.4))
        if not (0 <= row < rows):
            continue
        c = col
        for ch in txt:
            w = _w(ch)
            if 0 <= c < cols:
                g[row][c] = ch
                if w == 2 and c + 1 < cols:
                    g[row][c + 1] = ""
            c += w
    return ["".join(r).rstrip() for r in g]


async def main():
    from bin.ai_tui import _build_tui, _enable_alt_enter_keys
    from bin.ai_lib import mode as _mode
    _enable_alt_enter_keys()
    ctx = {"lang": "chinese", "session_id": str(uuid.uuid4()), "memory_mode": "global",
           "cwd": os.getcwd(), "quiet": False, "show_time": True,
           "session_start": time.time(), "mode": "normal"}
    App = _build_tui()
    app = App({"user_home_dir": tempfile.mkdtemp(prefix="onyx_shot_")}, ctx)
    async with app.run_test(size=(COLS, ROWS)) as pilot:
        await pilot.pause(0.5)
        from rich.text import Text as RT
        from bin.ai_lib.ui import render_ai_panel, tui_plain
        _mode.set_render_mode("tui")
        try:
            app._log(app._turn_rule())
            app._log(RT("❯ 帮我把 utils.py 的 parse 重构一下", style="bold #CBD5E1"))
            app._log("")
            app._log_renderable(render_ai_panel(
                "## 重构方案\n\n把 `parse` 拆成三阶段：\n\n1. 词法 `_scan()`\n2. 组装 `_assemble()`\n\n"
                "> 保持签名 `parse(src) -> Node` 不变。\n\n```python\ndef parse(src):\n    return _assemble(_scan(src))\n```\n"))
            # 工具块：与 ai_cmd 实际输出一致（空行 → 🔧 行 → → 结果行 → 空行）
            app._log("")
            app._log("  \x1b[1;32m🔧 Read\x1b[0m \x1b[36mpath=utils.py\x1b[0m")
            app._log("   \x1b[2m→ 128 行，3 个函数：parse / _scan / _assemble\x1b[0m")
            app._log("")
            app._log("  \x1b[1;32m🔧 Edit\x1b[0m \x1b[36mpath=utils.py\x1b[0m")
            app._log("   \x1b[2m→ +12 −8\x1b[0m")
            app._log("")
            app._log_renderable(tui_plain("utils.py: 已重构", title="✅ Edit utils.py",
                                          border_style="dim green"))
            app._render_todos([
                {"content": "读取 utils.py", "status": "completed", "activeForm": "读取"},
                {"content": "重构 parse", "status": "in_progress", "activeForm": "重构 parse"},
                {"content": "跑测试", "status": "pending", "activeForm": "跑测试"}])
            app._render_status({"cwd": os.getcwd(), "ctx": 17214, "cache_pct": 87.3, "balance": "¥42.10"})
            app._set_thinking(True)
            app._set_subagent_activity("explore · 扫描仓库中…")
        except Exception as e:
            print("inject:", e, file=sys.stderr)
        await pilot.pause(0.7)
        svg = app.export_screenshot()
        open(OUT + ".svg", "w", encoding="utf-8").write(svg)
        out = grid_of(svg, COLS, ROWS)
        open(OUT, "w", encoding="utf-8").write("\n".join(out) + "\n")
        print("\n".join(out))


if __name__ == "__main__":
    asyncio.run(main())
