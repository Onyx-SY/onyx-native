#!/usr/bin/env python3
"""Main.py 依赖安装器回归（不真装任何包）。

覆盖：
  1. 批量检测：**单次**子进程查完全部 import 名（旧实现每库一次，Termux 首启 13~17 次）；
  2. 批量失败 → 回退逐库检测；
  3. 安装：PEP 668（externally-managed-environment）→ 自动加 --break-system-packages；
  4. 主镜像失败 → 备用镜像重试；
  5. pip 失败输出不再被吞（能看到原因）+ 平台化修复提示；
  6. --force-check 真正校验依赖（源码断言）。

运行: python3 test/virtual/test_main_installer.py
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from Main import (  # noqa: E402
    UltraFastEnvironmentChecker as Checker, _BATCH_CHECK_SRC, REQUIRED_DEPENDENCIES,
)


class _Stub:
    """只提供被测方法需要的属性，避免构造完整检查器（会跑真实环境探测）。"""

    python_exe = sys.executable
    system_type = "Termux"
    _best_mirror_cache = None

    _mirror_chain = Checker._mirror_chain
    _lib_import_name = Checker._lib_import_name

    def test_mirror_speed(self):
        return REQUIRED_DEPENDENCIES["pip_mirrors"][0]


def test_batch_check_single_subprocess():
    calls = {"n": 0}
    real_run = subprocess.run

    def counting(*a, **k):
        calls["n"] += 1
        return real_run(*a, **k)

    import Main as _m
    orig = _m.subprocess.run
    _m.subprocess.run = counting
    try:
        stub = _Stub()
        names = ["json", "os", "definitely_missing_pkg_xyz", "tqdm"]
        missing = Checker.batch_check_libs(stub, names)
    finally:
        _m.subprocess.run = orig

    assert calls["n"] == 1, f"批量检测应只起 1 次子进程，实际 {calls['n']} 次"
    assert missing == ["definitely_missing_pkg_xyz"], f"缺失列表异常：{missing}"
    print(f"PASS 批量检测：{len(names)} 个库只用 1 次子进程，缺失项识别正确")


def test_batch_check_fallback():
    """批量检测失败（返回码非 0）→ 返回 None，调用方回退逐库。"""
    real_run = subprocess.run

    def broken(*a, **k):
        class R:
            returncode = 1
            stdout = ""
            stderr = "boom"
        return R()

    import Main as _m
    orig = _m.subprocess.run
    _m.subprocess.run = broken
    try:
        assert Checker.batch_check_libs(_Stub(), ["json"]) is None, "失败时应返回 None 以触发回退"
    finally:
        _m.subprocess.run = orig
    print("PASS 批量检测失败 → 返回 None（回退逐库检测）")


def test_import_name_mapping():
    stub = _Stub()
    assert Checker._lib_import_name(stub, "msgpack-python") == "msgpack"
    assert Checker._lib_import_name(stub, "argon2-cffi") == "argon2"
    assert Checker._lib_import_name(stub, "prompt_toolkit") == "prompt_toolkit"
    assert Checker._lib_import_name(stub, "some-pkg") == "some_pkg"
    print("PASS pip 包名 → import 名映射（含特例）")


def test_pep668_auto_break_system_packages():
    stub = _Stub()
    seen = []

    def fake_once(lib, mirror, no_cache=False, extra_args=()):
        seen.append(tuple(extra_args))
        if "--break-system-packages" in extra_args:
            return True, "Successfully installed"
        return False, ("error: externally-managed-environment\n"
                       "× This environment is externally managed")

    stub._pip_install_once = fake_once
    ok, out = Checker.install_single_lib(stub, "requests")
    assert ok is True, "识别 PEP 668 后应加 --break-system-packages 重试成功"
    assert any("--break-system-packages" in a for a in seen), f"未使用放行参数：{seen}"
    print("PASS PEP 668：externally-managed-environment → 自动 --break-system-packages 重试")


def test_mirror_fallback_and_visible_output():
    stub = _Stub()
    tried = []

    def fake_once(lib, mirror, no_cache=False, extra_args=()):
        tried.append(mirror)
        if mirror == REQUIRED_DEPENDENCIES["pip_mirrors"][0]:
            return False, "ERROR: Could not find a version that satisfies the requirement"
        return True, "Successfully installed"

    stub._pip_install_once = fake_once
    ok, out = Checker.install_single_lib(stub, "requests")
    assert ok is True, "主镜像失败后应回退到备用镜像"
    assert len(set(tried)) >= 2, f"未尝试备用镜像：{tried}"
    print(f"PASS 镜像回退：按顺序尝试 {len(set(tried))} 个镜像后成功")

    # 全失败时：输出必须带回来（旧实现 DEVNULL → 用户看不到任何原因）
    stub2 = _Stub()
    stub2._pip_install_once = lambda lib, m, no_cache=False, extra_args=(): (
        False, "gcc: not found\nfailed building wheel for xxx")
    ok2, out2 = Checker.install_single_lib(stub2, "xxx")
    assert ok2 is False and "failed building wheel" in out2, f"失败输出被吞：{out2!r}"
    hint = Checker._install_hint(stub2, out2)
    assert "pkg install" in hint, f"Termux 应提示 pkg install，实际 {hint!r}"
    print(f"PASS 失败输出可见 + 平台化提示：{hint}")


def test_install_hints():
    stub = _Stub()
    assert "PEP 668" in Checker._install_hint(stub, "externally-managed-environment")
    assert "pkg install" in Checker._install_hint(stub, "clang: not found")
    assert "manylinux" in Checker._install_hint(stub, "No matching distribution found")
    assert "网络" in Checker._install_hint(stub, "Read timed out")
    assert Checker._install_hint(stub, "something else") == ""

    stub_linux = _Stub()
    stub_linux.system_type = "Linux/macOS"
    assert "build-essential" in Checker._install_hint(stub_linux, "gcc: not found")
    print("PASS 修复提示：PEP668 / Termux / Debian / 无预编译包 / 网络 / 兜底")


def test_force_check_validates_libs():
    """--force-check 必须真正校验 python_libs（旧实现连它都不查）。"""
    src = open(os.path.join(ROOT, "Main.py"), encoding="utf-8").read()
    i = src.index("libs_missing: List[str] = []")
    seg = src[i:i + 1200]
    assert "if force_check:" in seg, "--force-check 分支缺失"
    assert "parallel_check_libs_fast" in seg, "--force-check 未调用依赖校验"
    assert "libs_missing" in src[src.index("check_results = {"):src.index("check_results = {") + 600], \
        "依赖校验结果应并入检查报告"
    print("PASS --force-check 真正校验 python_libs 并并入报告")

    # 非 force 路径不得引入额外开销（秒开特性不能被破坏）
    j = src.index("def ultra_fast_check")
    k = src.index("libs_missing: List[str] = []")
    assert "parallel_check_libs_fast" not in src[j:k], "非 force 路径不应做依赖检查"
    print("PASS 非 --force-check 路径仍不做依赖检查（保持秒开）")


def test_batch_src_runs():
    r = subprocess.run([sys.executable, "-c", _BATCH_CHECK_SRC,
                        json.dumps(["json", "definitely_not_here_xyz"])],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout.strip()) == ["definitely_not_here_xyz"]
    print("PASS 批量检测脚本可独立运行且结果正确")


def main():
    test_batch_src_runs()
    test_batch_check_single_subprocess()
    test_batch_check_fallback()
    test_import_name_mapping()
    test_pep668_auto_break_system_packages()
    test_mirror_fallback_and_visible_output()
    test_install_hints()
    test_force_check_validates_libs()
    print("\nALL PASS")


if __name__ == "__main__":
    main()
