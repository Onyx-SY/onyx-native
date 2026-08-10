#!/usr/bin/env python3
"""离线验证 resolve_path.py + ai_lib/sandbox.py 的 / 开头路径解析新语义：
解析后的虚拟根路径整条存在 → 解析；原路径真实存在或两者都不存在（如 /api
网络接口）→ 不解析、原样返回。

运行: python3 test/virtual/test_resolve_path.py
"""
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from lib import resolve_path as rp  # noqa: E402
from bin.ai_lib import sandbox  # noqa: E402


def _setup():
    _tmp = tempfile.mkdtemp(prefix="onyx_rp_test_")
    rp.ROOT_DIR = os.path.realpath(_tmp)
    rp.USER_HOME_DIR = os.path.realpath(os.path.join(_tmp, "home"))
    rp.PATH_RESOLVE_CACHE.clear()
    return _tmp


def test_abs_missing_returns_as_is():
    tmp = _setup()
    # /api：虚拟根下不存在、真实根下也不存在 → 不解析（可能是网络接口）
    r = rp.resolve_path("/api")
    assert r == "/api", f"期望原样返回 /api, 实际 {r}"
    # / 根目录映射
    assert rp.resolve_path("/") == tmp
    print("PASS /api 两者都不存在 → 不解析原样返回")


def test_abs_exists_in_virtual_root_resolves():
    tmp = _setup()
    os.makedirs(os.path.join(tmp, "etc"), exist_ok=True)
    with open(os.path.join(tmp, "etc", "app.conf"), "w") as f:
        f.write("x")
    r = rp.resolve_path("/etc/app.conf")
    assert r == os.path.join(tmp, "etc", "app.conf"), f"期望解析到虚拟根, 实际 {r}"
    print("PASS 虚拟根下完整存在 → 正常解析")


def test_abs_exists_in_real_root_still_resolves():
    _setup()
    if not os.path.exists("/system"):  # 仅 Android/类 Unix 有真实 /system
        print("SKIP 本机无真实 /system（非 Android 环境）")
        return
    r = rp.resolve_path("/system")
    assert r == os.path.join(rp.ROOT_DIR, "system"), f"真实存在也应解析（映射到虚拟根）, 实际 {r}"
    print("PASS 真实文件系统存在（/system）→ 仍解析映射到虚拟根")


def test_cache_cannot_override_missing():
    tmp = _setup()
    # 旧缓存污染场景：/api 曾缓存为虚拟根路径（TTL 内）
    rp.PATH_RESOLVE_CACHE["/api"] = (os.path.join(tmp, "api"), time.time(), os.getcwd())
    r = rp.resolve_path("/api")
    assert r == "/api", f"旧缓存不得覆盖不解析语义, 实际 {r}"
    print("PASS 旧缓存条目不覆盖：/api 仍原样返回")


def test_should_resolve_flags():
    _setup()
    assert rp._should_resolve("./x") is True
    assert rp._should_resolve("../x") is True
    assert rp._should_resolve("~/x") is True
    assert rp._should_resolve("..") is True
    assert rp._should_resolve("-") is True
    assert rp._should_resolve("plain") is False
    assert rp.resolve_path("/dev/null") == "/dev/null"  # 特殊路径保护
    print("PASS 相对/~/- 仍需解析；普通词与特殊路径不变")


def test_sandbox_resolve_same_semantics():
    tmp = _setup()
    sandbox.init(tmp, os.path.join(tmp, "home"), force=True)
    try:
        # /api 虚拟根下不存在 → 原样返回
        assert sandbox.resolve("/api") == "/api", "sandbox /api 应原样返回"
        # 虚拟根下存在 → 映射
        os.makedirs(os.path.join(tmp, "data"), exist_ok=True)
        with open(os.path.join(tmp, "data", "x.txt"), "w") as f:
            f.write("x")
        assert sandbox.resolve("/data/x.txt") == os.path.join(tmp, "data", "x.txt")
        # 真实文件系统存在 → 仍解析（映射到沙箱根）
        if os.path.exists("/system"):
            assert sandbox.resolve("/system") == os.path.join(tmp, "system")
        # 物理路径已在沙箱内 → 原样返回（不重复映射）
        phys = os.path.join(tmp, "data", "x.txt")
        assert sandbox.resolve(phys) == phys
        # 根目录映射
        assert sandbox.resolve("/") == tmp
        # 相对路径不受影响
        assert sandbox.resolve("./y") == os.path.join(tmp, "y")
        # 未激活 → 原样
        sandbox.deactivate()
        assert sandbox.resolve("/api") == "/api"
    finally:
        sandbox.deactivate()
    print("PASS sandbox.resolve 与 resolve_path 语义一致")


def _cleanup(tmp):
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    _d = _setup()
    try:
        test_abs_missing_returns_as_is()
        test_abs_exists_in_virtual_root_resolves()
        test_abs_exists_in_real_root_still_resolves()
        test_cache_cannot_override_missing()
        test_should_resolve_flags()
        test_sandbox_resolve_same_semantics()
        print("\nALL PASS")
    finally:
        _cleanup(_d)
