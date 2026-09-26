#!/usr/bin/env python3
"""脚本执行回归：shebang 优先 + Onyx 内部执行入口放行。

背景（实测）：
  `./a.sh`（文本脚本）原本一律被改写成
  `python <ROOT_DIR>/onyx/cmd.py -c "source ./a.sh"`；而 `<ROOT_DIR>/onyx/cmd.py`
  命中了 perm_path.json 的 `/onyx/<*:10>` 规则（min_mode=adv）→ mid 模式被拒。
修复：
  1. 脚本**有 shebang** → 交给系统执行（有 +x 给路径；没有 +x 用 shebang 解释器显式运行）；
  2. 无 shebang → 仍由 Onyx 自执行，但 `<ROOT_DIR>/onyx/cmd.py` 作为 **Onyx 内部执行入口**
     在细颗粒路径检查里直接放行（其内部命令仍走完整安全检查）。

运行: python3 test/virtual/test_shebang_exec.py
"""
import contextlib
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from lib.parse import read_shebang, _shebang_command, _build_shebang_cmd, \
    handle_executable_path  # noqa: E402
import lib.safe as safe  # noqa: E402


def _w(dirpath, name, content, mode=0o644):
    p = os.path.join(dirpath, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    os.chmod(p, mode)
    return p


def test_read_shebang():
    d = tempfile.mkdtemp(prefix="onyx_sb_")
    cases = {
        "sh.sh": ("#!/bin/sh\necho hi\n", "/bin/sh"),
        "env.sh": ("#!/usr/bin/env python3\nprint(1)\n", "/usr/bin/env python3"),
        "envs.sh": ("#!/usr/bin/env -S python3 -u\nprint(1)\n",
                    "/usr/bin/env -S python3 -u"),
        "none.sh": ("echo hi\nls\n", None),
        "blank.sh": ("\n#!/bin/sh\n", None),
        "txt.txt": ("hello world\n", None),
    }
    for name, (content, expect) in cases.items():
        p = _w(d, name, content)
        got = read_shebang(p)
        assert got == expect, f"{name}: 期望 {expect!r}，实际 {got!r}"
    assert read_shebang(os.path.join(d, "not-exist")) is None
    print("PASS read_shebang：普通 / env / env -S / 无 shebang / 首行非 shebang / 不存在")


def test_shebang_command():
    cases = [
        ("/bin/sh", ["/bin/sh"]),
        ("/bin/bash -e", ["/bin/bash", "-e"]),
        ("/usr/bin/env python3", ["python3"]),
        ("/usr/bin/env -S python3 -u", ["python3", "-u"]),
        ("/usr/bin/env -u FOO python3", ["python3"]),
        ("/usr/bin/env", []),
        ("", []),
    ]
    for body, expect in cases:
        got = _shebang_command(body)
        assert got == expect, f"{body!r}: 期望 {expect}，实际 {got}"
    print("PASS _shebang_command：env 前缀 / env -S / env -u / 纯解释器")


def test_handle_executable_path():
    d = tempfile.mkdtemp(prefix="onyx_sb_")
    exe = _w(d, "run.sh", "#!/bin/sh\necho hi\n", 0o755)
    noexe = _w(d, "noexec.sh", "#!/bin/sh\necho hi\n", 0o644)
    envpy = _w(d, "env.py", "#!/usr/bin/env python3\nprint(1)\n", 0o644)
    plain = _w(d, "plain.sh", "echo hi\nls\n", 0o644)
    binary = _w(d, "bin.dat", "\x7fELF" + "\x00" * 64, 0o755)

    def resolve(tok):
        return os.path.join(d, os.path.basename(tok))

    # ① 二进制可执行 → 原样给系统
    assert handle_executable_path("./bin.dat", resolve, "/ROOT") == binary
    # ② 有 shebang + 有 +x → 直接给路径（内核按 shebang 启动）
    assert handle_executable_path("./run.sh", resolve, "/ROOT") == exe
    # ③ 有 shebang 但没 +x → 用 shebang 解释器显式运行（否则 Permission denied）
    assert handle_executable_path("./noexec.sh", resolve, "/ROOT") == f"/bin/sh {noexe}"
    assert handle_executable_path("./env.py", resolve, "/ROOT") == f"python3 {envpy}"
    # ④ 无 shebang → Onyx 自执行（source 语义）
    assert handle_executable_path("./plain.sh", resolve, "/ROOT") == \
        'python /ROOT/onyx/cmd.py -c "source ./plain.sh"'
    # ⑤ 解析失败 / 非文件 → 原样返回
    assert handle_executable_path("./ghost.sh", resolve, "/ROOT") == "./ghost.sh"
    print("PASS handle_executable_path：二进制 / shebang+x / shebang 无 x / 无 shebang 自执行")


def test_internal_entry_exempt():
    """cmd.py 作为 Onyx 内部入口放行；同目录其它路径仍按规则拦截。

    用 /storage/... 前缀复现真实场景：它命中 perm_path.json 的 `/<*:1>` 兜底规则
    （whitelist + min_mode=adv，白名单里只有 python3 没有 python）。
    """
    root = "/storage/emulated/0/onyx-test"
    user_home = os.path.join(root, "home", "u0")

    safe.PERM_PATH_CONFIG_LOADED = False
    safe.PERM_PATH_CONFIG = []
    safe.load_perm_path_config(root, "u0", None)
    assert safe._ROOT_DIR == root

    entries = safe._onyx_internal_entry_paths()
    assert os.path.join(root, "onyx", "cmd.py") in entries, entries

    class _Mode:
        current_mode = "mid"

    def _check(paths):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            r = safe.check_path_permission_for_cmd("python", paths, "u0", _Mode(),
                                                   user_home=user_home)
        return r, buf.getvalue()

    ok_cmd, out_cmd = _check([os.path.join(root, "onyx", "cmd.py")])
    ok_other, out_other = _check([os.path.join(root, "onyx", "other.py")])
    assert ok_cmd is True, f"Onyx 内部入口应放行，实际被拦：{out_cmd}"
    assert ok_other is False, f"普通 /onyx/ 路径仍应被 min_mode 拦，实际放行：{out_other}"
    assert "需要 adv" in out_other or "Permission denied" in out_other
    print("PASS 内部入口豁免：cmd.py 放行，同目录其它路径仍被拦")

    # 复位，避免影响同进程后续用例
    safe.PERM_PATH_CONFIG_LOADED = False
    safe.PERM_PATH_CONFIG = []
    safe._ROOT_DIR = None


def test_build_shebang_cmd_quotes():
    d = tempfile.mkdtemp(prefix="onyx_sb_")
    p = _w(d, "q.sh", "#!/bin/sh\n", 0o644)
    assert _build_shebang_cmd("/bin/sh", p, True) == f'/bin/sh "{p}"'
    assert _build_shebang_cmd("/bin/sh", p, False) == f"/bin/sh {p}"
    print("PASS 引号保持")


def main():
    test_read_shebang()
    test_shebang_command()
    test_handle_executable_path()
    test_build_shebang_cmd_quotes()
    test_internal_entry_exempt()
    print("\nALL PASS")


if __name__ == "__main__":
    main()
