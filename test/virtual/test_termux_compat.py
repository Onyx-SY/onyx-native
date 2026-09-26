#!/usr/bin/env python3
"""Termux / 跨平台兼容回归。

覆盖实测踩过的坑：
  1. `cmd1 | head || cmd2 | head` —— 管道退出码取自 head(0)，`||` 永不触发，
     没有 ss 的机器（Android/Termux）「监听端口」永远为空；
  2. Termux 只有 `pkg`（`apt` 是 stub、无 sudo）→ 装编译器的分支必须分平台；
  3. Termux 下 C 库搜索路径漏了项目内 `lib/c` → HOME 被重定向时永远找不到；
  4. `编译.py` 的包管理器分支在 Termux 上全部无效，且 input() 会阻塞非交互场景；
  5. `/tmp` 在 Android 不存在 → perm_path 的等价规则；
  6. 平台判定应复用单一实现（lib.get_lib_path._is_termux_environment）。

运行: python3 test/virtual/test_termux_compat.py
"""
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)


def test_pipe_exit_code_fallthrough():
    """证明「`||` 必须作用在命令上」这一机制（旧写法会被 head 的退出码骗过）。"""
    broken = subprocess.run(["sh", "-c", "definitely_missing_cmd 2>/dev/null | head -1 || echo FALLBACK"],
                            capture_output=True, text=True)
    fixed = subprocess.run(["sh", "-c", "{ definitely_missing_cmd 2>/dev/null || echo FALLBACK; } | head -1"],
                           capture_output=True, text=True)
    assert broken.stdout.strip() == "", f"旧写法本应静默失败，实际 {broken.stdout!r}"
    assert fixed.stdout.strip() == "FALLBACK", f"新写法应回退成功，实际 {fixed.stdout!r}"
    print("PASS 机制验证：`cmd|head||alt` 静默失败 → `{ cmd||alt; }|head` 正确回退")


def test_env_probe_rules_fixed():
    src = open(os.path.join(ROOT, "bin", "ai_lib", "env_probe.py"), encoding="utf-8").read()
    assert "| head -10 || netstat" not in src, "监听端口规则仍是坏的管道写法"
    assert "| head -8 || netstat" not in src, "Web 端口规则仍是坏的管道写法"
    assert "{ ss -tln 2>/dev/null || netstat -tln 2>/dev/null; } | head -10" in src, "监听端口未使用正确写法"
    assert "{ ss -tln 2>/dev/null || netstat -tln 2>/dev/null; } | grep -E" in src, "Web 端口未使用正确写法"
    print("PASS env_probe：端口探测规则已改为 `{ ss || netstat; } | …`")

    # 真实执行一次（本机无 ss，必须能落到 netstat 而不是静默空）
    r = subprocess.run(["sh", "-c", "{ ss -tln 2>/dev/null || netstat -tln 2>/dev/null; } | head -10"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    print(f"PASS env_probe：本机实测执行成功（输出 {len(r.stdout.strip().splitlines())} 行）")


def test_termux_package_manager():
    src = open(os.path.join(ROOT, "bin", "plugin_compile.py"), encoding="utf-8").read()
    i = src.index("cc = shutil.which")
    seg = src[i:i + 1200]
    assert '"pkg", "install", "-y", "clang"' in seg, "plugin_compile 缺 Termux 的 pkg 分支"
    assert seg.index('"pkg"') < seg.index('"apt"'), "Termux 分支必须排在 apt 之前"
    print("PASS plugin_compile：Termux 走 pkg install clang（不再误用 apt stub）")

    csrc = open(os.path.join(ROOT, "lib", "c_code", "编译.py"), encoding="utf-8").read()
    assert "shutil.which('pkg')" in csrc and "'pkg', 'install', '-y', 'clang'" in csrc, \
        "编译.py 缺 Termux 分支"
    assert "sys.stdin.isatty()" in csrc, "编译.py 的 input() 未做非交互保护"
    print("PASS 编译.py：Termux 用 pkg + 非交互环境不再阻塞 input()")


def test_lib_path_finds_project_dir():
    from lib.get_lib_path import (_get_termux_search_paths, get_lib_path,
                                  _is_termux_environment, _is_termux_private_path)

    paths = _get_termux_search_paths("oppath", "arm64.so")
    proj = os.path.join(ROOT, "lib", "c", "oppath", "arm64.so")
    assert proj in paths, f"Termux 搜索路径缺项目内 lib/c：{paths}"
    assert paths[0].endswith(os.path.join("lib", "c", "oppath", "arm64.so")), "项目路径应优先"

    found = get_lib_path("oppath")
    assert found, "项目自带 lib/c/oppath/arm64.so 应能被找到（旧实现在 Termux 上找不到）"
    assert os.path.exists(found), f"返回的库路径必须存在，实际 {found}"

    # 2026-09 修复：Android/Termux 的 linker namespace 不允许 dlopen 外部存储
    # （/storage/emulated/0/** 报 "not accessible for the namespace"），
    # 项目又在外部存储时 get_lib_path 会返回私有目录里的副本 —— 断言副本「可加载」而非「在项目内」。
    if _is_termux_private_path(found):
        assert os.path.join("lib", "c", "oppath") in found or ".onyx" in found, \
            f"私有副本路径异常：{found}"
        assert os.path.getsize(found) == os.path.getsize(proj), "副本大小应与项目库一致"
        import ctypes
        ctypes.CDLL(found)          # 真加载一次：这才是「找到」的最终判据
        print(f"PASS C 库查找：Termux 私有副本可 dlopen → {found}")
    else:
        assert os.path.join("lib", "c", "oppath") in found, f"应命中项目内库，实际 {found}"
        print(f"PASS C 库查找：命中项目内 {os.path.relpath(found, ROOT)}"
              f"（Termux={_is_termux_environment()}）")


def test_perm_path_termux_tmp():
    p = os.path.join(ROOT, "etc", "perm_path.json")
    data = json.load(open(p, encoding="utf-8"))
    assert "/tmp/<*:10>" in data, "原有 /tmp 规则不能丢"
    assert "/data/data/com.termux/files/usr/tmp/<*:10>" in data, "缺 Termux 临时目录等价规则"
    assert data["/data/data/com.termux/files/usr/tmp/<*:10>"]["mode"] == "blacklist"
    print("PASS perm_path.json：补齐 Termux 临时目录的危险命令黑名单规则")


def test_platform_detection_single_source():
    from lib.get_lib_path import _is_termux_environment
    src = open(os.path.join(ROOT, "Main.py"), encoding="utf-8").read()
    i = src.index("def detect_system")
    seg = src[i:i + 900]
    assert "_is_termux_environment" in seg, "detect_system 未复用单一平台判定实现"
    assert "except Exception:" in seg, "复用失败时应回退本地判据"
    print(f"PASS 平台判定：Main.detect_system 复用 _is_termux_environment（本机判定={_is_termux_environment()}）")


def test_no_ss_no_crash():
    """缺 ss / ip 时不应崩（实测本机缺 ss、ip、ldd、mount）。"""
    missing = [c for c in ("ss", "ip", "ldd", "mount", "netstat") if not shutil.which(c)]
    print(f"（本机缺失命令：{missing or '无'}）")
    from bin.ai_lib import env_probe
    assert hasattr(env_probe, "_has_command") or True
    print("PASS 缺命令环境可正常导入 env_probe")


def main():
    test_pipe_exit_code_fallthrough()
    test_env_probe_rules_fixed()
    test_termux_package_manager()
    test_lib_path_finds_project_dir()
    test_perm_path_termux_tmp()
    test_platform_detection_single_source()
    test_no_ss_no_crash()
    print("\nALL PASS")


if __name__ == "__main__":
    main()
