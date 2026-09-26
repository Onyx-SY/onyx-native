# -*- coding: utf-8 -*-
"""M3：Main.py —— --force-check 真正校验依赖 + 修关键裸 except + 版本号。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, "Main.py")
CFG = os.path.join(ROOT, "etc", "config.json")

P = []


def patch(path, old, new):
    P.append((path, old, new))


# ── 1) --force-check 真正校验（并顺带补齐）Python 依赖 ──
patch(
    MAIN,
    """            with TimeIt("加载配置文件"):
                config_valid = self.load_config()
            
            check_results = {
                'status': 'success',
                'start_file': 'Onyx.py',
                'system_type': self.system_type,
                'python_exe': self.python_exe,
                'pip_exe': self.pip_exe,
                'config_valid': config_valid,
                'timestamp': time.time()
            }""",
    """            with TimeIt("加载配置文件"):
                config_valid = self.load_config()
            
            # --force-check：真正校验 Python 依赖库。
            # 旧实现（含 --force-check）从不检查 python_libs，依赖被删/装坏后只能在
            # import 阶段崩，报错是难懂的 ImportError。这里显式检查并顺手补齐。
            libs_missing: List[str] = []
            if force_check:
                with TimeIt("校验Python依赖库"):
                    try:
                        libs_missing = self.parallel_check_libs_fast(
                            REQUIRED_DEPENDENCIES["python_libs"])
                        if libs_missing:
                            print(f"  ⚠ 缺失依赖: {', '.join(libs_missing)}")
                            _ok_libs, _failed_libs = self.parallel_install_libs(libs_missing)
                            libs_missing = list(_failed_libs)
                            if _failed_libs:
                                print(f"  ✗ 仍有缺失: {', '.join(_failed_libs)}")
                            else:
                                print(f"  ✓ 依赖已补齐（{len(_ok_libs)} 个）")
                        else:
                            print("  ✓ Python 依赖齐全")
                    except Exception as e:
                        log_print(f"依赖校验异常: {e}", is_error=True)
            
            check_results = {
                'status': 'success',
                'start_file': 'Onyx.py',
                'system_type': self.system_type,
                'python_exe': self.python_exe,
                'pip_exe': self.pip_exe,
                'config_valid': config_valid,
                'libs_missing': libs_missing,
                'timestamp': time.time()
            }""",
)

# ── 2) 关键裸 except → 带原因 ──
patch(
    MAIN,
    """        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            return True
        except:
            return False""",
    """        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            return True
        except Exception as e:
            log_print(f"配置文件读取失败: {e}", is_error=True)
            return False""",
)

patch(
    MAIN,
    """try:
    import getpass as _getpass
    USER = _getpass.getuser()
except:
    USER = os.environ.get("USER", os.environ.get("USERNAME", "default"))""",
    """try:
    import getpass as _getpass
    USER = _getpass.getuser()
except Exception:
    USER = os.environ.get("USER", os.environ.get("USERNAME", "default"))""",
)

# ── 3) 版本号 ──
patch(
    CFG,
    """"version": "2.10.0.b1",""",
    """"version": "2.10.1.b1",""",
)


def main():
    cache = {}
    for path, old, new in P:
        if path not in cache:
            with open(path, encoding="utf-8") as f:
                cache[path] = f.read()
        text = cache[path]
        n = text.count(old)
        if n != 1:
            print(f"FAIL {os.path.basename(path)}: 命中 {n} 次\n{old[:200]}")
            return 1
        cache[path] = text.replace(old, new, 1)
    for path, text in cache.items():
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"OK   {os.path.relpath(path, ROOT)}")
    print("PATCH_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
