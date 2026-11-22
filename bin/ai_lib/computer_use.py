# -*- coding: utf-8 -*-
"""
computer_use.py — 跨平台桌面操控（AI 的「手」）

设计要点
--------
1. **零硬依赖**：优先调用系统命令（xdotool / wmctrl / scrot / grim / ydotool /
   powershell / am），Python 库（pywinauto / mss / PIL / pyatspi）只做可选增强。
   缺什么就在返回值里写明缺什么、怎么装，不抛异常。
2. **看的是文本，不是图片**：Onyx 的工具返回值只有纯字符串（无多模态通道），
   所以「看屏幕」走 `ui_tree`（控件树文本化）+ `find`（按名/角色查找）。
   `screenshot` 只落盘留档，返回路径 / 尺寸 / sha1 / 是否变化。
3. **控件寻址优先于坐标**：`ui_tree` / `find` 会把节点缓存成索引表，
   `click` 可直接用 `control=<索引>` 点击，比算坐标稳。
4. **错误约定**：失败一律返回以 `error:` 开头的字符串（框架只认这个前缀）。

配置文件：~/.ai_s/computer_use/config.json
截图目录：~/.ai_s/computer_use/shots/
节点缓存：~/.ai_s/computer_use/nodes.json
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

MODULE_VERSION = "1.0.0"

# ── 动作集合 ──
ACTIONS = (
    "capability", "screenshot", "ui_tree", "find", "click", "type", "key",
    "scroll", "drag", "window", "launch",
)

# ── 默认配置 ──
DEFAULT_CONFIG: Dict[str, Any] = {
    "backend": "auto",        # auto / x11 / wayland / windows / android / null
    "shots_dir": "",          # 空 = ~/.ai_s/computer_use/shots
    "timeout": 10,            # 单条外部命令超时（秒）
    "max_tree_nodes": 300,    # ui_tree 最多返回节点数
    "max_depth": 6,           # ui_tree 最大深度
    "max_output_chars": 6000, # 单次返回字符上限
    "type_delay_ms": 20,      # 逐字输入间隔
    "safe_mode": True,        # 拦截高危按键组合
}

# ── 高危按键组合（safe_mode=true 时拦截）──
KEY_BLACKLIST = (
    "ctrl+alt+delete", "ctrl+alt+del", "alt+sysrq", "ctrl+alt+f1",
    "ctrl+alt+f2", "ctrl+alt+f3", "ctrl+alt+f4", "ctrl+alt+f5",
    "ctrl+alt+f6", "ctrl+alt+f7",
)

# ── 鼠标按钮映射 ──
BUTTON_MAP_X11 = {"left": 1, "middle": 2, "right": 3}

# 滚动方向 → xdotool 按钮号
SCROLL_BUTTON_X11 = {"up": 4, "down": 5, "left": 6, "right": 7}


# ══════════════════════════════════════════════════════════════
# 基础工具函数
# ══════════════════════════════════════════════════════════════

def _home() -> str:
    return os.path.expanduser("~")


def root_dir() -> str:
    """~/.ai_s/computer_use"""
    return os.path.join(_home(), ".ai_s", "computer_use")


def config_path() -> str:
    return os.path.join(root_dir(), "config.json")


def shots_dir(cfg: Optional[Dict[str, Any]] = None) -> str:
    cfg = cfg or load_config()
    custom = (cfg.get("shots_dir") or "").strip()
    return custom or os.path.join(root_dir(), "shots")


def nodes_path() -> str:
    return os.path.join(root_dir(), "nodes.json")


def _atomic_write(path: str, data: str, mode: int = 0o600) -> None:
    """原子写文件（先写临时文件再 rename），失败静默。"""
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = "%s.tmp.%d" % (path, os.getpid())
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
        try:
            os.chmod(path, mode)
        except OSError:
            pass
    except Exception:
        pass


def _deep_defaults(cfg: Any, defaults: Dict[str, Any]) -> Dict[str, Any]:
    """用默认值补齐缺失键（浅合并即可：配置是扁平结构）。"""
    out = dict(defaults)
    if isinstance(cfg, dict):
        for k, v in cfg.items():
            if k.startswith("_"):
                continue
            out[k] = v
    return out


def load_config() -> Dict[str, Any]:
    """读取配置；损坏时回退默认值（不抛异常）。"""
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            raw = json.load(f)
        return _deep_defaults(raw, DEFAULT_CONFIG)
    except Exception:
        return dict(DEFAULT_CONFIG)


def save_config(cfg: Dict[str, Any]) -> None:
    payload = {k: v for k, v in cfg.items() if not str(k).startswith("_")}
    _atomic_write(config_path(), json.dumps(payload, ensure_ascii=False, indent=2))


def _which(name: str) -> Optional[str]:
    try:
        return shutil.which(name)
    except Exception:
        return None


def _have(*names: str) -> Dict[str, Optional[str]]:
    return {n: _which(n) for n in names}


def _default_runner(argv: List[str], timeout: float,
                    input_text: Optional[str] = None) -> Tuple[int, str, str]:
    """执行外部命令，返回 (returncode, stdout, stderr)；不抛异常。"""
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout,
            input=input_text, errors="replace",
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except FileNotFoundError:
        return 127, "", "command not found: %s" % (argv[0] if argv else "")
    except subprocess.TimeoutExpired:
        return 124, "", "timeout after %ss" % timeout
    except Exception as exc:  # noqa: BLE001
        return 1, "", repr(exc)


def _cap(text: str, limit: int = 6000) -> str:
    """截断超长输出（保留头部 + 尾部）。"""
    if text is None:
        return ""
    if limit <= 0 or len(text) <= limit:
        return text
    head = int(limit * 0.7)
    tail = limit - head
    return "%s\n... [truncated %d chars] ...\n%s" % (
        text[:head], len(text) - limit, text[-tail:],
    )


def _sha1_file(path: str) -> str:
    h = hashlib.sha1()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def _png_size(path: str) -> Tuple[int, int]:
    """零依赖读 PNG 宽高（IHDR）。失败返回 (0, 0)。"""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
        if len(head) >= 24 and head[:8] == b"\x89PNG\r\n\x1a\n":
            width = int.from_bytes(head[16:20], "big")
            height = int.from_bytes(head[20:24], "big")
            return width, height
    except Exception:
        pass
    return 0, 0


def detect_platform() -> str:
    """判定当前平台：windows / android / wayland / x11 / unknown。"""
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    # Android（Termux）：/system/bin/am 存在且 /system/build.prop 存在
    if os.path.exists("/system/bin/am") or os.environ.get("ANDROID_ROOT"):
        return "android"
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "unknown"


# ── 节点缓存（ui_tree / find → click 的索引桥梁）──

def save_nodes(nodes: List[Dict[str, Any]]) -> None:
    _atomic_write(nodes_path(), json.dumps(nodes, ensure_ascii=False, indent=1))


def load_nodes() -> List[Dict[str, Any]]:
    try:
        with open(nodes_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════
# 后端基类
# ══════════════════════════════════════════════════════════════

class Backend:
    """平台后端基类。所有方法返回字符串；失败以 `error:` 开头。"""

    name = "null"
    platform = "unknown"
    SUPPORTED = frozenset({"capability"})

    def __init__(self, cfg: Dict[str, Any], runner=None):
        self.cfg = cfg or dict(DEFAULT_CONFIG)
        self._runner = runner or _default_runner
        self._nodes: List[Dict[str, Any]] = []
        self._last_shot: Dict[str, str] = {}

    # ── 能力 ──
    def tools(self) -> Dict[str, Optional[str]]:
        return {}

    def available(self) -> bool:
        return False

    def notes(self) -> List[str]:
        return []

    def supports(self, action: str) -> bool:
        return action in self.SUPPORTED

    def capability(self) -> Dict[str, Any]:
        return {
            "backend": self.name,
            "platform": self.platform,
            "available": self.available(),
            "tools": self.tools(),
            "actions": {a: self.supports(a) for a in ACTIONS},
            "notes": self.notes(),
        }

    # ── 执行辅助 ──
    def run(self, argv: List[str], timeout: Optional[float] = None,
            input_text: Optional[str] = None) -> Tuple[int, str, str]:
        t = timeout if timeout is not None else float(self.cfg.get("timeout", 10))
        return self._runner(argv, t, input_text)

    def _need(self, *tools: str) -> Optional[str]:
        """检查命令是否存在；缺则返回 error 文本。"""
        missing = [t for t in tools if not _which(t)]
        if missing:
            return "error: 缺少命令 %s（%s 后端需要）" % (
                ", ".join(missing), self.name,
            )
        return None

    def _unsupported(self, action: str, hint: str = "") -> str:
        msg = "error: 后端 %s 不支持 action=%s" % (self.name, action)
        if hint:
            msg += "（%s）" % hint
        return msg

    def _fail(self, detail: str) -> str:
        return "error: %s" % detail

    # ── 动作（子类覆盖）──
    def screenshot(self, save_to: str = "", **kw: Any) -> str:
        return self._unsupported("screenshot")

    def ui_tree(self, limit: int = 0, depth: int = 0, **kw: Any) -> str:
        return self._unsupported("ui_tree")

    def find(self, pattern: str = "", role: str = "", **kw: Any) -> str:
        return self._unsupported("find")

    def click(self, x: Any = None, y: Any = None, control: Any = None,
              button: str = "left", count: int = 1, **kw: Any) -> str:
        return self._unsupported("click")

    def type_text(self, text: str = "", **kw: Any) -> str:
        return self._unsupported("type")

    def key(self, key: str = "", **kw: Any) -> str:
        return self._unsupported("key")

    def scroll(self, x: Any = None, y: Any = None, amount: int = 3,
               direction: str = "down", **kw: Any) -> str:
        return self._unsupported("scroll")

    def drag(self, x: Any = None, y: Any = None, to_x: Any = None,
             to_y: Any = None, **kw: Any) -> str:
        return self._unsupported("drag")

    def window(self, title: str = "", op: str = "list", **kw: Any) -> str:
        return self._unsupported("window")

    def launch(self, app: str = "", **kw: Any) -> str:
        return self._unsupported("launch")

    # ── 截图公共后处理 ──
    def _finish_shot(self, path: str, note: str = "") -> str:
        if not os.path.exists(path):
            return self._fail("截图失败：文件未生成 %s" % path)
        size = os.path.getsize(path)
        if size <= 0:
            return self._fail("截图失败：文件为空 %s" % path)
        w, h = _png_size(path)
        sha = _sha1_file(path)
        prev = self._last_shot.get(path)
        changed = "yes" if prev != sha else "no"
        self._last_shot[path] = sha
        lines = [
            "screenshot ok",
            "  path: %s" % path,
            "  size: %d bytes" % size,
            "  dims: %dx%d" % (w, h) if w and h else "  dims: unknown",
            "  sha1: %s" % sha,
            "  changed: %s" % changed,
        ]
        if note:
            lines.append("  note: %s" % note)
        return "\n".join(lines)


class NullBackend(Backend):
    """兜底后端：什么都不支持，但 capability 能报告原因。"""

    name = "null"
    platform = "unknown"

    def notes(self) -> List[str]:
        return ["未识别到可用桌面环境（无 DISPLAY / WAYLAND_DISPLAY / Windows / Android）"]

    def launch(self, app: str = "", **kw: Any) -> str:
        return self._fail("无可用后端，无法启动应用")


# ══════════════════════════════════════════════════════════════
# Android 后端（轻量：启动 App / 打开 URL / Intent）
# ══════════════════════════════════════════════════════════════

class AndroidBackend(Backend):
    """Android/Termux：只能「敲门」，不能「进去」。

    - 可做：launch（am start）、open URL、Intent
    - 不可做：screenshot / click / type / key / ui_tree（需 root 或无障碍桥）
    """

    name = "android"
    platform = "android"
    SUPPORTED = frozenset({"capability", "launch", "window"})

    def tools(self) -> Dict[str, Optional[str]]:
        return _have("am", "termux-open", "termux-open-url")

    def available(self) -> bool:
        return bool(_which("am"))

    def notes(self) -> List[str]:
        return [
            "Android 无 root：screencap/input/uiautomator 均需 shell 权限，不可用",
            "要获得点击/读屏能力需装 AutoX.js（无障碍）或 Termux:API + root",
        ]

    def launch(self, app: str = "", **kw: Any) -> str:
        app = (app or "").strip()
        if not app:
            return self._fail("launch 需要 app 参数（包名 / pkg/.Activity / URL / 文件路径）")
        missing = self._need("am")
        if missing:
            return missing

        if re.match(r"^(https?|mailto|tel|weixin|alipays?):", app, re.I):
            rc, out, err = self.run(["am", "start", "-a", "android.intent.action.VIEW", "-d", app])
            return self._am_result(rc, out, err, "open url %s" % app)

        if app.startswith("/") or app.startswith("~"):
            missing = self._need("termux-open")
            if missing:
                return missing
            rc, out, err = self.run(["termux-open", os.path.expanduser(app)])
            return self._am_result(rc, out, err, "open file %s" % app)

        # 显式组件名（pkg/.Activity）→ 直接 -n，最可靠
        if "/" in app:
            rc, out, err = self.run(["am", "start", "-n", app])
            return self._am_result(rc, out, err, "launch %s" % app)

        # 只给包名：依次尝试 LAUNCHER 类别与常见主 Activity
        attempts = [
            ["am", "start", "-a", "android.intent.action.MAIN",
             "-c", "android.intent.category.LAUNCHER", "-p", app],
        ] + [["am", "start", "-n", app + s]
             for s in ("/.ui.LauncherUI", "/.MainActivity", "/.SplashActivity")]
        tries = []
        for argv in attempts:
            rc, out, err = self.run(argv)
            text = ((out or "") + (err or "")).strip()
            if rc == 0 and "Error" not in text:
                return self._am_result(rc, out, err, "launch package %s" % app)
            tries.append("%s → %s" % (" ".join(argv[3:]) or argv[-1],
                                      text.replace("\n", " ")[:140] or ("rc=%d" % rc)))
        return self._fail(
            "launch package %s 失败（%d 种入口均未成功）：\n%s\n"
            "  提示：Android 10+ 限制后台启动 Activity。把 Termux 切到前台后重试，或给 Termux 授予"
            "「显示在其他应用上层 / 后台弹出界面」权限；也可以直接传组件名（pkg/.Activity）。"
            % (app, len(attempts), "\n".join("  - " + t for t in tries)))

    def _am_result(self, rc: int, out: str, err: str, what: str) -> str:
        text = ((out or "") + (err or "")).strip()
        if rc != 0 or "Error" in text or "does not exist" in text:
            return self._fail("%s 失败：%s" % (what, text or ("rc=%d" % rc)))
        return "launch ok\n  what: %s\n  output: %s" % (what, text or "(no output)")

    def window(self, title: str = "", op: str = "list", **kw: Any) -> str:
        return self._unsupported(
            "window", "Android 无法枚举窗口；如需前台应用请用 launch 或 adb dumpsys",
        )


# ══════════════════════════════════════════════════════════════
# Linux X11 后端（xdotool + scrot/maim + wmctrl + 可选 pyatspi）
# ══════════════════════════════════════════════════════════════

# 用系统 python 跑 AT-SPI 导出控件树（pyatspi 只能装在系统解释器里）
_ATSPI_DUMP = r'''
import json, sys
try:
    import pyatspi
except Exception as exc:
    print(json.dumps({"error": "pyatspi missing: %r" % (exc,)}))
    sys.exit(0)
MAX_NODES = int(sys.argv[1]) if len(sys.argv) > 1 else 300
MAX_DEPTH = int(sys.argv[2]) if len(sys.argv) > 2 else 6
out = []
def walk(node, depth):
    if len(out) >= MAX_NODES or depth > MAX_DEPTH:
        return
    try:
        role = node.getRoleName()
        name = (node.name or "").strip()
        ext = node.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
        rect = [int(ext.x), int(ext.y), int(ext.width), int(ext.height)]
    except Exception:
        role, name, rect = "unknown", "", [0, 0, 0, 0]
    if name or role in ("push button", "entry", "text", "menu item", "check box",
                        "radio button", "combo box", "page tab", "list item"):
        out.append({"i": len(out), "role": role, "name": name, "rect": rect, "d": depth})
    try:
        for child in node:
            walk(child, depth + 1)
    except Exception:
        pass
try:
    desktop = pyatspi.Registry.getDesktop(0)
    for app in desktop:
        walk(app, 0)
except Exception as exc:
    print(json.dumps({"error": "atspi walk failed: %r" % (exc,)}))
    sys.exit(0)
print(json.dumps({"nodes": out}, ensure_ascii=False))
'''


class X11Backend(Backend):
    name = "x11"
    platform = "linux-x11"
    SUPPORTED = frozenset({
        "capability", "screenshot", "ui_tree", "find", "click", "type",
        "key", "scroll", "drag", "window", "launch",
    })

    def tools(self) -> Dict[str, Optional[str]]:
        return _have("xdotool", "wmctrl", "scrot", "maim", "import")

    def available(self) -> bool:
        return bool(_which("xdotool")) and bool(os.environ.get("DISPLAY"))

    def notes(self) -> List[str]:
        notes = []
        if not _which("xdotool"):
            notes.append("缺 xdotool：apt install xdotool（点击/输入/按键必需）")
        if not any(_which(t) for t in ("scrot", "maim", "import")):
            notes.append("缺截图工具：apt install scrot（或 maim / imagemagick）")
        if not _which("wmctrl"):
            notes.append("缺 wmctrl：apt install wmctrl（窗口管理）")
        return notes

    # ── 截图 ──
    def screenshot(self, save_to: str = "", **kw: Any) -> str:
        path = self._shot_path(save_to)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        attempts = []
        if _which("scrot"):
            attempts.append((["scrot", "-o", "-z", path], "scrot"))
        if _which("maim"):
            attempts.append((["maim", path], "maim"))
        if _which("import"):
            attempts.append((["import", "-window", "root", path], "import"))
        if not attempts:
            return self._fail("无截图工具（apt install scrot 或 maim）")
        last = ""
        for argv, tag in attempts:
            rc, out, err = self.run(argv)
            if rc == 0 and os.path.exists(path):
                return self._finish_shot(path, "via %s" % tag)
            last = "%s rc=%d %s" % (tag, rc, (err or out).strip()[:200])
        return self._fail("截图失败：%s" % last)

    def _shot_path(self, save_to: str = "") -> str:
        if save_to:
            return os.path.abspath(os.path.expanduser(save_to))
        d = shots_dir(self.cfg)
        return os.path.join(d, "x11-%s.png" % time.strftime("%Y%m%d-%H%M%S"))

    # ── 控件树 ──
    def ui_tree(self, limit: int = 0, depth: int = 0, **kw: Any) -> str:
        limit = int(limit or self.cfg.get("max_tree_nodes", 300))
        depth = int(depth or self.cfg.get("max_depth", 6))
        nodes, err = self._atspi_nodes(limit, depth)
        if err:
            # 降级：窗口列表
            rc, out, _ = self.run(["wmctrl", "-l"])
            if rc == 0 and out.strip():
                lines = ["ui_tree degraded（%s），仅窗口级：" % err]
                for i, ln in enumerate(out.strip().splitlines()):
                    lines.append("  [%d] window %s" % (i, ln))
                return "\n".join(lines)
            return self._fail("ui_tree 不可用：%s（可 apt install python3-pyatspi）" % err)
        if not nodes:
            return "ui_tree ok（0 个可访问节点；该窗口可能未启用无障碍）"
        self._nodes = nodes
        save_nodes(nodes)
        return self._render_nodes(nodes, "ui_tree")

    def _atspi_nodes(self, limit: int, depth: int) -> Tuple[List[Dict[str, Any]], str]:
        py = shutil.which("python3") or shutil.which("python")
        if not py:
            return [], "无 python3"
        rc, out, err = self.run([py, "-c", _ATSPI_DUMP, str(limit), str(depth)], timeout=20)
        if rc != 0 or not out.strip():
            return [], (err or "pyatspi 导出失败").strip()[:200]
        try:
            data = json.loads(out.strip().splitlines()[-1])
        except Exception:
            return [], "AT-SPI 输出解析失败"
        if isinstance(data, dict) and data.get("error"):
            return [], str(data["error"])[:200]
        nodes = data.get("nodes") if isinstance(data, dict) else None
        return (nodes or []), ""

    def _render_nodes(self, nodes: List[Dict[str, Any]], title: str) -> str:
        lines = ["%s ok（%d 个节点）" % (title, len(nodes))]
        for n in nodes:
            rect = n.get("rect") or [0, 0, 0, 0]
            cx, cy = rect[0] + rect[2] // 2, rect[1] + rect[3] // 2
            lines.append('  [%d] %s "%s" rect=%d,%d,%d,%d center=%d,%d' % (
                n.get("i", 0), n.get("role", "?"), (n.get("name") or "")[:60],
                rect[0], rect[1], rect[2], rect[3], cx, cy,
            ))
        return _cap("\n".join(lines), int(self.cfg.get("max_output_chars", 6000)))

    # ── 查找 ──
    def find(self, pattern: str = "", role: str = "", **kw: Any) -> str:
        if not pattern and not role:
            return self._fail("find 需要 pattern 或 role")
        if not self._nodes:
            tree = self.ui_tree()
            if tree.startswith("error:"):
                return tree
        rx = None
        if pattern:
            try:
                rx = re.compile(pattern, re.I)
            except re.error as exc:
                return self._fail("pattern 正则非法：%s" % exc)
        hits = []
        for n in self._nodes:
            if role and role.lower() not in str(n.get("role", "")).lower():
                continue
            if rx and not rx.search(str(n.get("name", ""))):
                continue
            hits.append(n)
        if not hits:
            return "find ok（0 个匹配）"
        return self._render_nodes(hits, "find")

    # ── 点击 ──
    def click(self, x: Any = None, y: Any = None, control: Any = None,
              button: str = "left", count: int = 1, **kw: Any) -> str:
        missing = self._need("xdotool")
        if missing:
            return missing
        px, py = self._resolve_point(x, y, control)
        if isinstance(px, str):
            return px
        btn = BUTTON_MAP_X11.get(str(button).lower(), 1)
        times = max(1, int(count or 1))
        rc, out, err = self.run([
            "xdotool", "mousemove", str(px), str(py),
            "click", "--repeat", str(times), "--delay", "80", str(btn),
        ])
        if rc != 0:
            return self._fail("click 失败：%s" % (err or out).strip()[:200])
        return "click ok\n  at: %d,%d\n  button: %s\n  count: %d" % (px, py, button, times)

    def _resolve_point(self, x: Any, y: Any, control: Any) -> Tuple[Any, Any]:
        """返回 (x, y) 或 (error_str, None)。"""
        if control not in (None, ""):
            nodes = self._nodes or load_nodes()
            try:
                idx = int(control)
            except (TypeError, ValueError):
                return ("error: control 必须是整数索引", None)
            if not nodes:
                return ("error: 无节点缓存，请先调用 ui_tree", None)
            if idx < 0 or idx >= len(nodes):
                return ("error: control 索引越界（0..%d）" % (len(nodes) - 1), None)
            rect = nodes[idx].get("rect") or [0, 0, 0, 0]
            return (rect[0] + rect[2] // 2, rect[1] + rect[3] // 2)
        if x is None or y is None:
            return ("error: click 需要 x/y 或 control", None)
        try:
            return (int(x), int(y))
        except (TypeError, ValueError):
            return ("error: x/y 必须是整数", None)

    # ── 输入 ──
    def type_text(self, text: str = "", **kw: Any) -> str:
        missing = self._need("xdotool")
        if missing:
            return missing
        if text is None or text == "":
            return self._fail("type 需要 text")
        delay = int(self.cfg.get("type_delay_ms", 20))
        rc, out, err = self.run([
            "xdotool", "type", "--clearmodifiers", "--delay", str(delay), "--", text,
        ])
        if rc != 0:
            return self._fail("type 失败：%s" % (err or out).strip()[:200])
        return "type ok（%d 字符）" % len(text)

    def key(self, key: str = "", **kw: Any) -> str:
        missing = self._need("xdotool")
        if missing:
            return missing
        key = (key or "").strip()
        if not key:
            return self._fail("key 需要键名，如 ctrl+s / Return / alt+Tab")
        if self.cfg.get("safe_mode", True) and key.lower() in KEY_BLACKLIST:
            return self._fail("按键 %s 被 safe_mode 拦截" % key)
        rc, out, err = self.run(["xdotool", "key", "--clearmodifiers", key])
        if rc != 0:
            return self._fail("key 失败：%s" % (err or out).strip()[:200])
        return "key ok: %s" % key

    # ── 滚动 ──
    def scroll(self, x: Any = None, y: Any = None, amount: int = 3,
               direction: str = "down", **kw: Any) -> str:
        missing = self._need("xdotool")
        if missing:
            return missing
        d = str(direction or "down").lower()
        if d not in SCROLL_BUTTON_X11:
            return self._fail("direction 必须是 up/down/left/right")
        times = max(1, abs(int(amount or 3)))
        argv = ["xdotool"]
        if x is not None and y is not None:
            try:
                argv += ["mousemove", str(int(x)), str(int(y))]
            except (TypeError, ValueError):
                return self._fail("x/y 必须是整数")
        argv += ["click", "--repeat", str(times), "--delay", "40", str(SCROLL_BUTTON_X11[d])]
        rc, out, err = self.run(argv)
        if rc != 0:
            return self._fail("scroll 失败：%s" % (err or out).strip()[:200])
        return "scroll ok: %s x%d" % (d, times)

    # ── 拖拽 ──
    def drag(self, x: Any = None, y: Any = None, to_x: Any = None,
             to_y: Any = None, **kw: Any) -> str:
        missing = self._need("xdotool")
        if missing:
            return missing
        try:
            sx, sy, ex, ey = int(x), int(y), int(to_x), int(to_y)
        except (TypeError, ValueError):
            return self._fail("drag 需要整数 x/y/to_x/to_y")
        rc, out, err = self.run([
            "xdotool", "mousemove", str(sx), str(sy), "mousedown", "1",
            "mousemove", str(ex), str(ey), "mouseup", "1",
        ])
        if rc != 0:
            return self._fail("drag 失败：%s" % (err or out).strip()[:200])
        return "drag ok: %d,%d → %d,%d" % (sx, sy, ex, ey)

    # ── 窗口 ──
    def window(self, title: str = "", op: str = "list", **kw: Any) -> str:
        op = (op or "list").lower()
        if op == "list":
            if not _which("wmctrl"):
                missing = self._need("xdotool")
                if missing:
                    return missing
                rc, out, err = self.run(["xdotool", "search", "--name", ""])
                if rc != 0:
                    return self._fail("窗口列表失败：%s" % (err or out).strip()[:200])
                ids = [ln.strip() for ln in out.splitlines() if ln.strip()]
                return "window list ok（%d 个窗口，xdotool）\n%s" % (
                    len(ids), "\n".join("  id=%s" % i for i in ids[:50]))
            rc, out, err = self.run(["wmctrl", "-l"])
            if rc != 0:
                return self._fail("窗口列表失败：%s" % (err or out).strip()[:200])
            return "window list ok（wmctrl）\n%s" % (out.strip() or "(空)")
        missing = self._need("wmctrl")
        if missing:
            return missing
        if not title:
            return self._fail("window op=%s 需要 title" % op)
        flag = {"activate": "-a", "focus": "-a", "close": "-c",
                "minimize": "-b add,hidden"}.get(op)
        if not flag:
            return self._fail("window op 必须是 list/activate/close/minimize")
        argv = ["wmctrl", flag, title] if op != "minimize" else ["wmctrl", "-r", title, "-b", "add,hidden"]
        rc, out, err = self.run(argv)
        if rc != 0:
            return self._fail("window %s 失败：%s" % (op, (err or out).strip()[:200]))
        return "window %s ok: %s" % (op, title)

    # ── 启动 ──
    def launch(self, app: str = "", **kw: Any) -> str:
        app = (app or "").strip()
        if not app:
            return self._fail("launch 需要 app（可执行名或命令行）")
        try:
            argv = app if isinstance(app, list) else shlex.split(app)
            proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, start_new_session=True)
            time.sleep(0.4)
            return "launch ok\n  cmd: %s\n  pid: %d" % (" ".join(argv), proc.pid)
        except Exception as exc:  # noqa: BLE001
            return self._fail("launch 失败：%r" % (exc,))


# ══════════════════════════════════════════════════════════════
# Linux Wayland 后端（grim 截图 + ydotool 输入）
# ══════════════════════════════════════════════════════════════

# 常用键名 → Linux evdev keycode（ydotool key 用数字）
_EVDEV_KEYS = {
    "esc": 1, "escape": 1, "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7,
    "7": 8, "8": 9, "9": 10, "0": 11, "minus": 12, "equal": 13,
    "backspace": 14, "tab": 15, "q": 16, "w": 17, "e": 18, "r": 19, "t": 20,
    "y": 21, "u": 22, "i": 23, "o": 24, "p": 25, "leftbrace": 26,
    "rightbrace": 27, "enter": 28, "return": 28, "ctrl": 29, "control": 29,
    "a": 30, "s": 31, "d": 32, "f": 33, "g": 34, "h": 35, "j": 36, "k": 37,
    "l": 38, "semicolon": 39, "apostrophe": 40, "grave": 41, "shift": 42,
    "backslash": 43, "z": 44, "x": 45, "c": 46, "v": 47, "b": 48, "n": 49,
    "m": 50, "comma": 51, "dot": 52, "slash": 53, "alt": 56, "space": 57,
    "capslock": 58, "f1": 59, "f2": 60, "f3": 61, "f4": 62, "f5": 63,
    "f6": 64, "f7": 65, "f8": 66, "f9": 67, "f10": 68, "f11": 87, "f12": 88,
    "home": 102, "up": 103, "pageup": 104, "left": 105, "right": 106,
    "end": 107, "down": 108, "pagedown": 109, "insert": 110, "delete": 111,
    "super": 125, "meta": 125, "win": 125,
}

# ydotool click 的按钮码
BUTTON_MAP_YDOTOOL = {"left": "0xC0", "right": "0xC1", "middle": "0xC2"}


class WaylandBackend(Backend):
    name = "wayland"
    platform = "linux-wayland"
    SUPPORTED = frozenset({
        "capability", "screenshot", "ui_tree", "find", "click", "type",
        "key", "drag", "window", "launch",
    })

    def tools(self) -> Dict[str, Optional[str]]:
        return _have("grim", "ydotool", "wtype", "swaymsg", "hyprctl", "kdotool")

    def available(self) -> bool:
        return bool(os.environ.get("WAYLAND_DISPLAY")) and bool(
            _which("ydotool") or _which("wtype"))

    def notes(self) -> List[str]:
        notes = []
        if not _which("grim"):
            notes.append("缺 grim：apt install grim（截图）")
        if not _which("ydotool"):
            notes.append("缺 ydotool：apt install ydotool（输入；需 uinput 权限）")
        if not _which("wtype"):
            notes.append("缺 wtype：apt install wtype（文本输入备选）")
        return notes

    # ── 截图 ──
    def screenshot(self, save_to: str = "", **kw: Any) -> str:
        missing = self._need("grim")
        if missing:
            return missing
        path = os.path.abspath(os.path.expanduser(save_to)) if save_to else os.path.join(
            shots_dir(self.cfg), "wayland-%s.png" % time.strftime("%Y%m%d-%H%M%S"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        rc, out, err = self.run(["grim", path])
        if rc != 0:
            return self._fail("grim 截图失败：%s" % (err or out).strip()[:200])
        return self._finish_shot(path, "via grim")

    # ── 控件树：Wayland 无全局无障碍，降级为窗口列表 ──
    def ui_tree(self, limit: int = 0, depth: int = 0, **kw: Any) -> str:
        wins = self._window_titles()
        if isinstance(wins, str):
            return wins
        lines = ["ui_tree degraded（Wayland 无全局无障碍树），窗口级 %d 个：" % len(wins)]
        for i, t in enumerate(wins[:limit or 50]):
            lines.append("  [%d] window %s" % (i, t))
        return "\n".join(lines)

    def _window_titles(self) -> Any:
        if _which("swaymsg"):
            rc, out, err = self.run(["swaymsg", "-t", "get_tree", "-r"])
            if rc == 0 and out.strip():
                try:
                    data = json.loads(out)
                except Exception:
                    return self._fail("swaymsg 输出解析失败")
                titles: List[str] = []

                def walk(node: Any) -> None:
                    if isinstance(node, dict):
                        if node.get("name") and node.get("type") in ("con", "floating_con"):
                            titles.append(str(node["name"]))
                        for child in node.get("nodes") or []:
                            walk(child)
                        for child in node.get("floating_nodes") or []:
                            walk(child)

                walk(data)
                return titles
        if _which("hyprctl"):
            rc, out, _ = self.run(["hyprctl", "clients", "-j"])
            if rc == 0 and out.strip():
                try:
                    return [str(c.get("title", "")) for c in json.loads(out)]
                except Exception:
                    pass
        if _which("kdotool"):
            rc, out, _ = self.run(["kdotool", "search", "--name", ""])
            if rc == 0:
                return [ln.strip() for ln in out.splitlines() if ln.strip()]
        return self._fail("Wayland 窗口枚举需要 swaymsg / hyprctl / kdotool")

    def find(self, pattern: str = "", role: str = "", **kw: Any) -> str:
        wins = self._window_titles()
        if isinstance(wins, str):
            return wins
        try:
            rx = re.compile(pattern or ".*", re.I)
        except re.error as exc:
            return self._fail("pattern 正则非法：%s" % exc)
        hits = [t for t in wins if rx.search(t)]
        if not hits:
            return "find ok（0 个匹配）"
        return "find ok（%d）\n%s" % (len(hits), "\n".join("  window %s" % h for h in hits))

    # ── 点击 ──
    def click(self, x: Any = None, y: Any = None, control: Any = None,
              button: str = "left", count: int = 1, **kw: Any) -> str:
        missing = self._need("ydotool")
        if missing:
            return missing
        if x is None or y is None:
            return self._fail("Wayland click 需要 x/y 坐标（无控件树索引）")
        try:
            px, py = int(x), int(y)
        except (TypeError, ValueError):
            return self._fail("x/y 必须是整数")
        btn = BUTTON_MAP_YDOTOOL.get(str(button).lower(), "0xC0")
        rc, out, err = self.run(["ydotool", "mousemove", "--absolute", "-x", str(px), "-y", str(py)])
        if rc != 0:
            return self._fail("ydotool mousemove 失败：%s" % (err or out).strip()[:200])
        for _ in range(max(1, int(count or 1))):
            rc, out, err = self.run(["ydotool", "click", btn])
            if rc != 0:
                return self._fail("ydotool click 失败：%s" % (err or out).strip()[:200])
            time.sleep(0.08)
        return "click ok\n  at: %d,%d\n  button: %s" % (px, py, button)

    # ── 输入 ──
    def type_text(self, text: str = "", **kw: Any) -> str:
        if text is None or text == "":
            return self._fail("type 需要 text")
        if _which("wtype"):
            rc, out, err = self.run(["wtype", "--", text])
            if rc == 0:
                return "type ok（%d 字符，wtype）" % len(text)
        missing = self._need("ydotool")
        if missing:
            return missing
        rc, out, err = self.run(["ydotool", "type", "--", text])
        if rc != 0:
            return self._fail("type 失败：%s" % (err or out).strip()[:200])
        return "type ok（%d 字符，ydotool）" % len(text)

    def key(self, key: str = "", **kw: Any) -> str:
        key = (key or "").strip()
        if not key:
            return self._fail("key 需要键名，如 ctrl+s / Return")
        if self.cfg.get("safe_mode", True) and key.lower() in KEY_BLACKLIST:
            return self._fail("按键 %s 被 safe_mode 拦截" % key)
        if _which("wtype"):
            rc, out, err = self.run(["wtype", "-k", key.replace("+", "-")])
            if rc == 0:
                return "key ok: %s（wtype）" % key
        missing = self._need("ydotool")
        if missing:
            return missing
        codes = []
        for part in re.split(r"\s*\+\s*", key.lower()):
            code = _EVDEV_KEYS.get(part)
            if code is None:
                return self._fail("未知键名 %r（Wayland 仅支持常见键）" % part)
            codes.append(code)
        seq = []
        for c in codes:
            seq.append("%d:1" % c)
        for c in reversed(codes):
            seq.append("%d:0" % c)
        rc, out, err = self.run(["ydotool", "key"] + seq)
        if rc != 0:
            return self._fail("key 失败：%s" % (err or out).strip()[:200])
        return "key ok: %s" % key

    # ── 拖拽 ──
    def drag(self, x: Any = None, y: Any = None, to_x: Any = None,
             to_y: Any = None, **kw: Any) -> str:
        missing = self._need("ydotool")
        if missing:
            return missing
        try:
            sx, sy, ex, ey = int(x), int(y), int(to_x), int(to_y)
        except (TypeError, ValueError):
            return self._fail("drag 需要整数 x/y/to_x/to_y")
        for argv in (
            ["ydotool", "mousemove", "--absolute", "-x", str(sx), "-y", str(sy)],
            ["ydotool", "click", "0x40"],   # 左键按下
            ["ydotool", "mousemove", "--absolute", "-x", str(ex), "-y", str(ey)],
            ["ydotool", "click", "0x80"],   # 左键抬起
        ):
            rc, out, err = self.run(argv)
            if rc != 0:
                return self._fail("drag 失败：%s" % (err or out).strip()[:200])
            time.sleep(0.05)
        return "drag ok: %d,%d → %d,%d" % (sx, sy, ex, ey)

    def scroll(self, x: Any = None, y: Any = None, amount: int = 3,
               direction: str = "down", **kw: Any) -> str:
        return self._fail(
            "Wayland 下滚轮注入依赖 ydotool 的 wheel 支持，当前版本不支持；"
            "可改用 XWayland 应用 + xdotool，或装 wtype/sway 后用键盘翻页")

    # ── 窗口 ──
    def window(self, title: str = "", op: str = "list", **kw: Any) -> str:
        op = (op or "list").lower()
        if op == "list":
            wins = self._window_titles()
            if isinstance(wins, str):
                return wins
            return "window list ok（%d）\n%s" % (
                len(wins), "\n".join("  %s" % t for t in wins[:50]) or "(空)")
        if not title:
            return self._fail("window op=%s 需要 title" % op)
        if _which("swaymsg"):
            cmd = {"activate": "[title=%s] focus", "focus": "[title=%s] focus",
                   "close": "[title=%s] kill"}.get(op)
            if not cmd:
                return self._fail("Wayland 仅支持 list/focus/close")
            rc, out, err = self.run(["swaymsg", cmd % json.dumps(title)])
            if rc != 0:
                return self._fail("swaymsg 失败：%s" % (err or out).strip()[:200])
            return "window %s ok: %s" % (op, title)
        if _which("kdotool"):
            argv = {"activate": ["kdotool", "windowactivate", title],
                    "focus": ["kdotool", "windowactivate", title],
                    "close": ["kdotool", "windowclose", title]}.get(op)
            if not argv:
                return self._fail("Wayland 仅支持 list/focus/close")
            rc, out, err = self.run(argv)
            if rc != 0:
                return self._fail("kdotool 失败：%s" % (err or out).strip()[:200])
            return "window %s ok: %s" % (op, title)
        return self._fail("Wayland 窗口操作需要 swaymsg 或 kdotool")

    def launch(self, app: str = "", **kw: Any) -> str:
        app = (app or "").strip()
        if not app:
            return self._fail("launch 需要 app")
        try:
            argv = shlex.split(app)
            proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, start_new_session=True)
            time.sleep(0.4)
            return "launch ok\n  cmd: %s\n  pid: %d" % (" ".join(argv), proc.pid)
        except Exception as exc:  # noqa: BLE001
            return self._fail("launch 失败：%r" % (exc,))


# ══════════════════════════════════════════════════════════════
# Windows 后端（ctypes user32 输入 + PowerShell 截图 + UIA/Win32 控件树）
# ══════════════════════════════════════════════════════════════

# PowerShell 截图脚本（System.Drawing，零第三方依赖）
_WIN_SHOT_PS = (
    "$ErrorActionPreference='Stop';"
    "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
    "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen;"
    "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
    "$g=[System.Drawing.Graphics]::FromImage($bmp);"
    "$g.CopyFromScreen($b.Left,$b.Top,0,0,$bmp.Size);"
    "$bmp.Save('%s',[System.Drawing.Imaging.ImageFormat]::Png);"
    "$g.Dispose();$bmp.Dispose()"
)

# 常用键名 → Windows 虚拟键码（VK）
_VK_KEYS = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "return": 0x0D,
    "shift": 0x10, "ctrl": 0x11, "control": 0x11, "alt": 0x12, "pause": 0x13,
    "capslock": 0x14, "esc": 0x1B, "escape": 0x1B, "space": 0x20,
    "pageup": 0x21, "pagedown": 0x22, "end": 0x23, "home": 0x24,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "insert": 0x2D, "delete": 0x2E, "win": 0x5B, "super": 0x5B,
    "numlock": 0x90, "scrolllock": 0x91,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
}


class WindowsBackend(Backend):
    name = "windows"
    platform = "windows"
    SUPPORTED = frozenset({
        "capability", "screenshot", "ui_tree", "find", "click", "type",
        "key", "scroll", "drag", "window", "launch",
    })

    def tools(self) -> Dict[str, Optional[str]]:
        return _have("powershell", "pwsh")

    def available(self) -> bool:
        return sys.platform.startswith("win")

    def notes(self) -> List[str]:
        notes = []
        if not (_which("powershell") or _which("pwsh")):
            notes.append("缺 powershell：截图需要它（Windows 自带，检查 PATH）")
        try:
            import uiautomation  # noqa: F401
            notes.append("已检测到 uiautomation 库：ui_tree 使用完整 UIA 控件树")
        except Exception:
            notes.append("未装 uiautomation（pip install uiautomation）：ui_tree 降级为 Win32 窗口/控件枚举")
        return notes

    # ── ctypes 句柄 ──
    def _user32(self):
        import ctypes
        return ctypes.windll.user32

    # ── 截图 ──
    def screenshot(self, save_to: str = "", **kw: Any) -> str:
        path = os.path.abspath(os.path.expanduser(save_to)) if save_to else os.path.join(
            shots_dir(self.cfg), "win-%s.png" % time.strftime("%Y%m%d-%H%M%S"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        ps = _which("powershell") or _which("pwsh")
        if not ps:
            return self._fail("缺 powershell，无法截图")
        script = _WIN_SHOT_PS % path.replace("'", "''")
        rc, out, err = self.run([ps, "-NoProfile", "-NonInteractive", "-Command", script], timeout=30)
        if rc != 0 or not os.path.exists(path):
            return self._fail("截图失败：%s" % (err or out).strip()[:300])
        return self._finish_shot(path, "via PowerShell System.Drawing")

    # ── 控件树 ──
    def ui_tree(self, limit: int = 0, depth: int = 0, **kw: Any) -> str:
        limit = int(limit or self.cfg.get("max_tree_nodes", 300))
        depth = int(depth or self.cfg.get("max_depth", 6))
        nodes = self._uia_nodes(limit, depth)
        src = "UIA"
        if nodes is None:
            nodes = self._win32_nodes(limit, depth)
            src = "Win32"
        if isinstance(nodes, str):
            return nodes
        if not nodes:
            return "ui_tree ok（0 个节点）"
        self._nodes = nodes
        save_nodes(nodes)
        lines = ["ui_tree ok（%d 个节点，%s）" % (len(nodes), src)]
        for n in nodes:
            rect = n.get("rect") or [0, 0, 0, 0]
            cx, cy = rect[0] + rect[2] // 2, rect[1] + rect[3] // 2
            lines.append('  [%d] %s "%s" rect=%d,%d,%d,%d center=%d,%d' % (
                n.get("i", 0), n.get("role", "?"), (n.get("name") or "")[:60],
                rect[0], rect[1], rect[2], rect[3], cx, cy))
        return _cap("\n".join(lines), int(self.cfg.get("max_output_chars", 6000)))

    def _uia_nodes(self, limit: int, depth: int) -> Any:
        """优先用 uiautomation 库；未安装返回 None（触发降级）。"""
        try:
            import uiautomation as auto  # type: ignore
        except Exception:
            return None
        nodes: List[Dict[str, Any]] = []

        def walk(ctrl: Any, d: int) -> None:
            if len(nodes) >= limit or d > depth:
                return
            try:
                r = ctrl.BoundingRectangle
                nodes.append({
                    "i": len(nodes),
                    "role": str(ctrl.ControlTypeName),
                    "name": str(ctrl.Name or ""),
                    "rect": [int(r.left), int(r.top),
                             int(r.right - r.left), int(r.bottom - r.top)],
                    "d": d,
                })
                for child in ctrl.GetChildren():
                    walk(child, d + 1)
            except Exception:
                return

        try:
            walk(auto.GetRootControl(), 0)
        except Exception as exc:  # noqa: BLE001
            return "error: UIA 遍历失败：%r" % (exc,)
        return nodes

    def _win32_nodes(self, limit: int, depth: int) -> Any:
        """零依赖 Win32 枚举：顶层窗口 + 一级子控件。"""
        try:
            import ctypes
            from ctypes import wintypes
        except Exception as exc:  # noqa: BLE001
            return "error: ctypes 不可用：%r" % (exc,)
        try:
            user32 = self._user32()
            nodes: List[Dict[str, Any]] = []
            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

            def rect_of(hwnd: Any) -> List[int]:
                r = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(r))
                return [int(r.left), int(r.top),
                        int(r.right - r.left), int(r.bottom - r.top)]

            def text_of(hwnd: Any) -> str:
                n = user32.GetWindowTextLengthW(hwnd)
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                return buf.value

            def cb(hwnd: Any, _lp: Any) -> bool:
                if len(nodes) >= limit:
                    return False
                try:
                    if not user32.IsWindowVisible(hwnd):
                        return True
                    title = text_of(hwnd)
                    if not title:
                        return True
                    nodes.append({"i": len(nodes), "role": "window", "name": title,
                                  "rect": rect_of(hwnd), "d": 0})
                    if depth > 0:
                        def ccb(child: Any, _l: Any) -> bool:
                            if len(nodes) >= limit:
                                return False
                            cls = ctypes.create_unicode_buffer(128)
                            user32.GetClassNameW(child, cls, 128)
                            txt = text_of(child)
                            if txt or cls.value:
                                nodes.append({"i": len(nodes), "role": cls.value or "control",
                                              "name": txt, "rect": rect_of(child), "d": 1})
                            return True
                        user32.EnumChildWindows(hwnd, WNDENUMPROC(ccb), 0)
                except Exception:
                    pass
                return True

            user32.EnumWindows(WNDENUMPROC(cb), 0)
            return nodes
        except Exception as exc:  # noqa: BLE001
            return "error: Win32 枚举失败：%r" % (exc,)

    def find(self, pattern: str = "", role: str = "", **kw: Any) -> str:
        if not pattern and not role:
            return self._fail("find 需要 pattern 或 role")
        if not self._nodes:
            tree = self.ui_tree()
            if tree.startswith("error:"):
                return tree
        try:
            rx = re.compile(pattern or ".*", re.I)
        except re.error as exc:
            return self._fail("pattern 正则非法：%s" % exc)
        hits = [n for n in self._nodes
                if (not role or role.lower() in str(n.get("role", "")).lower())
                and rx.search(str(n.get("name", "")))]
        if not hits:
            return "find ok（0 个匹配）"
        lines = ["find ok（%d 个匹配）" % len(hits)]
        for n in hits:
            rect = n.get("rect") or [0, 0, 0, 0]
            lines.append('  [%d] %s "%s" center=%d,%d' % (
                n.get("i", 0), n.get("role", "?"), (n.get("name") or "")[:60],
                rect[0] + rect[2] // 2, rect[1] + rect[3] // 2))
        return "\n".join(lines)

    # ── 输入 ──
    def _move(self, x: int, y: int) -> None:
        self._user32().SetCursorPos(int(x), int(y))

    def click(self, x: Any = None, y: Any = None, control: Any = None,
              button: str = "left", count: int = 1, **kw: Any) -> str:
        if control not in (None, ""):
            nodes = self._nodes or load_nodes()
            try:
                idx = int(control)
            except (TypeError, ValueError):
                return self._fail("control 必须是整数索引")
            if not nodes:
                return self._fail("无节点缓存，请先调用 ui_tree")
            if idx < 0 or idx >= len(nodes):
                return self._fail("control 索引越界（0..%d）" % (len(nodes) - 1))
            rect = nodes[idx].get("rect") or [0, 0, 0, 0]
            px, py = rect[0] + rect[2] // 2, rect[1] + rect[3] // 2
        else:
            if x is None or y is None:
                return self._fail("click 需要 x/y 或 control")
            try:
                px, py = int(x), int(y)
            except (TypeError, ValueError):
                return self._fail("x/y 必须是整数")
        try:
            u = self._user32()
            self._move(px, py)
            flags = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010),
                     "middle": (0x0020, 0x0040)}.get(str(button).lower(), (0x0002, 0x0004))
            for _ in range(max(1, int(count or 1))):
                u.mouse_event(flags[0], 0, 0, 0, 0)
                u.mouse_event(flags[1], 0, 0, 0, 0)
                time.sleep(0.08)
        except Exception as exc:  # noqa: BLE001
            return self._fail("click 失败：%r" % (exc,))
        return "click ok\n  at: %d,%d\n  button: %s" % (px, py, button)

    def type_text(self, text: str = "", **kw: Any) -> str:
        if text is None or text == "":
            return self._fail("type 需要 text")
        try:
            u = self._user32()
            KEYEVENTF_UNICODE, KEYEVENTF_KEYUP = 0x0004, 0x0002
            for ch in text:
                code = ord(ch)
                u.keybd_event(0, code, KEYEVENTF_UNICODE, 0)
                u.keybd_event(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0)
                time.sleep(max(0, int(self.cfg.get("type_delay_ms", 20))) / 1000.0)
        except Exception as exc:  # noqa: BLE001
            return self._fail("type 失败：%r" % (exc,))
        return "type ok（%d 字符）" % len(text)

    def key(self, key: str = "", **kw: Any) -> str:
        key = (key or "").strip()
        if not key:
            return self._fail("key 需要键名，如 ctrl+s / enter")
        if self.cfg.get("safe_mode", True) and key.lower() in KEY_BLACKLIST:
            return self._fail("按键 %s 被 safe_mode 拦截" % key)
        try:
            u = self._user32()
            vks: List[int] = []
            for part in re.split(r"\s*\+\s*", key.lower()):
                vk = _VK_KEYS.get(part)
                if vk is None and len(part) == 1:
                    vk = ord(part.upper())
                if vk is None:
                    return self._fail("未知键名 %r" % part)
                vks.append(vk)
            for vk in vks:
                u.keybd_event(vk, 0, 0, 0)
            for vk in reversed(vks):
                u.keybd_event(vk, 0, 0x0002, 0)
        except Exception as exc:  # noqa: BLE001
            return self._fail("key 失败：%r" % (exc,))
        return "key ok: %s" % key

    def scroll(self, x: Any = None, y: Any = None, amount: int = 3,
               direction: str = "down", **kw: Any) -> str:
        try:
            u = self._user32()
            if x is not None and y is not None:
                self._move(int(x), int(y))
            d = str(direction or "down").lower()
            delta = 120 if d == "up" else -120
            for _ in range(max(1, abs(int(amount or 3)))):
                u.mouse_event(0x0800, 0, 0, delta, 0)  # MOUSEEVENTF_WHEEL
                time.sleep(0.03)
        except Exception as exc:  # noqa: BLE001
            return self._fail("scroll 失败：%r" % (exc,))
        return "scroll ok: %s x%d" % (direction, max(1, abs(int(amount or 3))))

    def drag(self, x: Any = None, y: Any = None, to_x: Any = None,
             to_y: Any = None, **kw: Any) -> str:
        try:
            u = self._user32()
            sx, sy, ex, ey = int(x), int(y), int(to_x), int(to_y)
            self._move(sx, sy)
            u.mouse_event(0x0002, 0, 0, 0, 0)
            steps = 12
            for i in range(1, steps + 1):
                self._move(sx + (ex - sx) * i // steps, sy + (ey - sy) * i // steps)
                time.sleep(0.02)
            u.mouse_event(0x0004, 0, 0, 0, 0)
        except (TypeError, ValueError):
            return self._fail("drag 需要整数 x/y/to_x/to_y")
        except Exception as exc:  # noqa: BLE001
            return self._fail("drag 失败：%r" % (exc,))
        return "drag ok: %d,%d → %d,%d" % (int(x), int(y), int(to_x), int(to_y))

    # ── 窗口 ──
    def window(self, title: str = "", op: str = "list", **kw: Any) -> str:
        op = (op or "list").lower()
        try:
            import ctypes
            from ctypes import wintypes
            u = self._user32()
        except Exception as exc:  # noqa: BLE001
            return self._fail("ctypes 不可用：%r" % (exc,))
        wins: List[Tuple[Any, str]] = []
        try:
            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

            def cb(hwnd: Any, _lp: Any) -> bool:
                try:
                    if u.IsWindowVisible(hwnd):
                        n = u.GetWindowTextLengthW(hwnd)
                        buf = ctypes.create_unicode_buffer(n + 1)
                        u.GetWindowTextW(hwnd, buf, n + 1)
                        if buf.value:
                            wins.append((hwnd, buf.value))
                except Exception:
                    pass
                return True

            u.EnumWindows(WNDENUMPROC(cb), 0)
        except Exception as exc:  # noqa: BLE001
            return self._fail("窗口枚举失败：%r" % (exc,))

        if op == "list":
            return "window list ok（%d）\n%s" % (
                len(wins), "\n".join('  hwnd=%s "%s"' % (int(h), t) for h, t in wins[:50]) or "(空)")
        if not title:
            return self._fail("window op=%s 需要 title" % op)
        match = [(h, t) for h, t in wins if title.lower() in t.lower()]
        if not match:
            return self._fail("未找到标题包含 %r 的窗口" % title)
        hwnd, real = match[0]
        try:
            if op in ("activate", "focus"):
                u.ShowWindow(hwnd, 9)      # SW_RESTORE
                u.SetForegroundWindow(hwnd)
            elif op == "minimize":
                u.ShowWindow(hwnd, 6)      # SW_MINIMIZE
            elif op == "close":
                u.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
            else:
                return self._fail("window op 必须是 list/activate/close/minimize")
        except Exception as exc:  # noqa: BLE001
            return self._fail("window %s 失败：%r" % (op, exc))
        return "window %s ok: %s" % (op, real)

    # ── 启动 ──
    def launch(self, app: str = "", **kw: Any) -> str:
        app = (app or "").strip()
        if not app:
            return self._fail("launch 需要 app（exe / 路径 / 命令行）")
        try:
            if os.path.exists(app):
                os.startfile(app)  # type: ignore[attr-defined]
                return "launch ok\n  via: os.startfile\n  app: %s" % app
            proc = subprocess.Popen(app, shell=True, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
            time.sleep(0.4)
            return "launch ok\n  cmd: %s\n  pid: %s" % (app, proc.pid)
        except Exception as exc:  # noqa: BLE001
            return self._fail("launch 失败：%r" % (exc,))


# ══════════════════════════════════════════════════════════════
# 后端选择与工具入口
# ══════════════════════════════════════════════════════════════

_BACKENDS = {
    "windows": WindowsBackend,
    "x11": X11Backend,
    "wayland": WaylandBackend,
    "android": AndroidBackend,
    "null": NullBackend,
}


def get_backend(cfg: Optional[Dict[str, Any]] = None, name: str = "auto",
                runner=None) -> Backend:
    """按配置/平台选择后端；不可用时逐级回退。"""
    cfg = cfg or load_config()
    plat = detect_platform()
    name = (name or cfg.get("backend") or "auto").lower()

    if name in ("auto", "", None):
        # 平台优先，其次按探测结果
        order = [plat]
        if plat == "android":
            order.append("null")
        elif plat == "unknown":
            order = ["x11", "wayland", "windows", "android", "null"]
        else:
            order.append("null")
    else:
        order = [name, "null"]

    for cand in order:
        cls = _BACKENDS.get(cand)
        if not cls:
            continue
        backend = cls(cfg, runner=runner)
        if cand == "null" or backend.available():
            return backend
    return NullBackend(cfg, runner=runner)


def render_capability(backend: Backend) -> str:
    cap = backend.capability()
    lines = [
        "capability ok",
        "  backend: %s" % cap["backend"],
        "  platform: %s" % cap["platform"],
        "  available: %s" % ("yes" if cap["available"] else "no"),
        "  actions:",
    ]
    for a in ACTIONS:
        mark = "✓" if cap["actions"].get(a) else "✗"
        lines.append("    %s %s" % (mark, a))
    tools = cap.get("tools") or {}
    if tools:
        lines.append("  tools:")
        for k, v in tools.items():
            lines.append("    %s: %s" % (k, v or "MISSING"))
    notes = cap.get("notes") or []
    if notes:
        lines.append("  notes:")
        for n in notes:
            lines.append("    - %s" % n)
    return "\n".join(lines)


def handle_computer_use(**params: Any) -> str:
    """computer_use 工具入口：按 action 路由到对应后端。

    所有失败返回 `error: ...`（框架只识别该前缀为错误）。
    """
    try:
        cfg = load_config()
        action = str(params.get("action") or "").strip().lower()
        if not action:
            return "error: 缺少 action 参数（可选：%s）" % " / ".join(ACTIONS)
        if action not in ACTIONS:
            return "error: 未知 action=%r（可选：%s）" % (action, " / ".join(ACTIONS))

        # 单次调用可临时覆盖超时
        try:
            if params.get("timeout"):
                cfg = dict(cfg)
                cfg["timeout"] = float(params["timeout"])
        except (TypeError, ValueError):
            pass

        backend = get_backend(cfg, str(params.get("backend") or cfg.get("backend") or "auto"))

        if action == "capability":
            return _cap(render_capability(backend),
                        int(cfg.get("max_output_chars", 6000)))

        if not backend.supports(action):
            return backend._unsupported(action)

        if action == "screenshot":
            out = backend.screenshot(save_to=str(params.get("save_to") or ""))
        elif action == "ui_tree":
            out = backend.ui_tree(limit=int(params.get("limit") or 0),
                                  depth=int(params.get("depth") or 0))
        elif action == "find":
            out = backend.find(pattern=str(params.get("pattern") or ""),
                               role=str(params.get("role") or ""))
        elif action == "click":
            out = backend.click(x=params.get("x"), y=params.get("y"),
                                control=params.get("control"),
                                button=str(params.get("button") or "left"),
                                count=int(params.get("count") or 1))
        elif action == "type":
            out = backend.type_text(text=str(params.get("text") or ""))
        elif action == "key":
            out = backend.key(key=str(params.get("key") or ""))
        elif action == "scroll":
            out = backend.scroll(x=params.get("x"), y=params.get("y"),
                                 amount=int(params.get("amount") or 3),
                                 direction=str(params.get("direction") or "down"))
        elif action == "drag":
            out = backend.drag(x=params.get("x"), y=params.get("y"),
                               to_x=params.get("to_x"), to_y=params.get("to_y"))
        elif action == "window":
            out = backend.window(title=str(params.get("title") or ""),
                                 op=str(params.get("op") or "list"))
        elif action == "launch":
            out = backend.launch(app=str(params.get("app") or ""))
        else:  # pragma: no cover - 上面已校验
            return "error: 未实现 action=%s" % action

        return _cap(out, int(cfg.get("max_output_chars", 6000)))
    except Exception as exc:  # noqa: BLE001
        return "error: computer_use 失败：%r" % (exc,)
