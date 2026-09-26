# -*- coding: utf-8 -*-
"""M2：Main.py 依赖检测与安装优化。

1. 批量检测：N 次子进程 → 1 次（find_spec 全查）；超时 1s → 15s（Android 冷启动会误判）；
   失败回退逐库；逐库回退路径改为**真并行**。
2. 安装：pip 输出不再 DEVNULL（失败可见）；主镜像失败 → 备用镜像重试；默认用缓存，
   最后一次回退才 --no-cache-dir；识别 PEP 668 自动加 --break-system-packages；
   失败时给平台化提示（Termux → pkg install clang）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, "Main.py")

P = []


def patch(old, new):
    P.append((old, new))


# ── 1) 模块级常量：批量检测脚本 ──
patch(
    """}

# 双语文本配置""",
    '''}

# 批量依赖检测：一次子进程问完所有 import 名（find_spec 不执行模块 → 无副作用、快）
_BATCH_CHECK_SRC = (
    "import importlib.util,json,sys\\n"
    "names=json.loads(sys.argv[1])\\n"
    "missing=[]\\n"
    "for n in names:\\n"
    "    try:\\n"
    "        if importlib.util.find_spec(n) is None:\\n"
    "            missing.append(n)\\n"
    "    except Exception:\\n"
    "        missing.append(n)\\n"
    "print(json.dumps(missing))\\n"
)

# 双语文本配置''',
)

# ── 2) 检测：批量 + 超时 + 真并行回退 ──
patch(
    '''@timer("check_lib_installed_fast")
    def check_lib_installed_fast(self, lib_name: str) -> bool:
        """快速检查库是否安装"""
        clean_lib = lib_name.split(';')[0].strip()
        
        import_name_map = {
            'pywinpty': 'winpty',
            'msgpack-python': 'msgpack',
            'pyreadline3': 'pyreadline3',
            'colorama': 'colorama',
            'argon2-cffi': 'argon2',
            'prompt_toolkit': 'prompt_toolkit',
            'requests': 'requests',
            'pygments': 'pygments',
            'tqdm': 'tqdm',
        }
        
        import_name = import_name_map.get(clean_lib, clean_lib.replace('-', '_'))
        
        try:
            subprocess.run([self.python_exe, "-c", f"import {import_name}"],
                           check=True, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=1)
            return True
        except:
            return False
    ''',
    '''@timer("check_lib_installed_fast")
    def check_lib_installed_fast(self, lib_name: str) -> bool:
        """检查单个库是否可导入（批量检测失败时的回退路径）。

        旧实现 timeout=1s：Android/Termux 冷启动一次解释器常需 200~600ms，
        高负载时超过 1s → 把「已安装」误判成「缺失」→ 反复重装。
        """
        clean_lib = lib_name.split(';')[0].strip()
        import_name = self._lib_import_name(clean_lib)

        try:
            subprocess.run([self.python_exe, "-c", f"import {import_name}"],
                           check=True, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=8)
            return True
        except Exception:
            return False

    def _lib_import_name(self, clean_lib: str) -> str:
        """pip 包名 → import 名（特例表 + `-`→`_` 兜底）。"""
        import_name_map = {
            'pywinpty': 'winpty',
            'msgpack-python': 'msgpack',
            'pyreadline3': 'pyreadline3',
            'colorama': 'colorama',
            'argon2-cffi': 'argon2',
            'prompt_toolkit': 'prompt_toolkit',
            'requests': 'requests',
            'pygments': 'pygments',
            'tqdm': 'tqdm',
        }
        return import_name_map.get(clean_lib, clean_lib.replace('-', '_'))

    @timer("batch_check_libs")
    def batch_check_libs(self, import_names: List[str]) -> Optional[List[str]]:
        """一次性检查多个库能否导入（**单次**子进程）。

        旧实现每个库起一次解释器（Termux 首启 13~17 次，每次 200~600ms 纯属浪费）。
        返回缺失的 import 名列表；任何异常返回 None，让调用方回退到逐库检测。
        """
        names = sorted({n for n in import_names if n})
        if not names:
            return []
        try:
            result = subprocess.run(
                [self.python_exe, "-c", _BATCH_CHECK_SRC, json.dumps(names)],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode != 0:
                return None
            lines = [ln for ln in (result.stdout or "").strip().splitlines() if ln.strip()]
            if not lines:
                return None
            missing = json.loads(lines[-1])
            if isinstance(missing, list):
                return [str(x) for x in missing]
        except Exception as e:
            log_print(f"批量依赖检测失败，回退逐库检测: {e}")
        return None
    ''',
)

patch(
    """        missing = []
        
        for lib in platform_filtered_libs:
            if not self.check_lib_installed_fast(lib):
                missing.append(lib)
        
        return missing""",
    """        if not platform_filtered_libs:
            return []

        # pip 包名 → import 名，一次子进程问完
        import_of = {lib: self._lib_import_name(lib.split(';')[0].strip())
                     for lib in platform_filtered_libs}
        missing_imports = self.batch_check_libs(list(import_of.values()))
        if missing_imports is not None:
            miss = set(missing_imports)
            return [lib for lib in platform_filtered_libs if import_of[lib] in miss]

        # 回退：逐库检测，但**真正并行**（旧实现名为 parallel 实为串行）
        try:
            with ThreadPoolExecutor(max_workers=min(8, len(platform_filtered_libs))) as executor:
                flags = list(executor.map(self.check_lib_installed_fast, platform_filtered_libs))
            return [lib for lib, ok in zip(platform_filtered_libs, flags) if not ok]
        except Exception:
            return [lib for lib in platform_filtered_libs
                    if not self.check_lib_installed_fast(lib)]""",
)

# ── 3) 镜像链 + 提示 + 安装（含 PEP 668 自适应）──
patch(
    '''    @timer("test_mirror_speed")
    def test_mirror_speed(self) -> str:
        """测试镜像源速度"""
        if hasattr(self, '_best_mirror_cache') and self._best_mirror_cache:
            return self._best_mirror_cache
        
        mirrors = REQUIRED_DEPENDENCIES["pip_mirrors"]
        best_mirror = mirrors[0]
        self._best_mirror_cache = best_mirror
        
        return best_mirror
    
    @timer("install_single_lib")
    def install_single_lib(self, lib_name: str, mirror: str) -> bool:
        """安装单个库"""
        try:
            pip_cmd = [self.python_exe, "-m", "pip", "install", "--no-cache-dir", "-i", mirror, lib_name]
            result = subprocess.run(pip_cmd, check=False, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=30)
            return result.returncode == 0
        except:
            return False
    ''',
    '''    @timer("test_mirror_speed")
    def test_mirror_speed(self) -> str:
        """返回主镜像（列表首项）。多镜像回退在 install_single_lib 里做。"""
        if hasattr(self, '_best_mirror_cache') and self._best_mirror_cache:
            return self._best_mirror_cache
        
        mirrors = REQUIRED_DEPENDENCIES["pip_mirrors"]
        best_mirror = mirrors[0]
        self._best_mirror_cache = best_mirror
        
        return best_mirror

    def _mirror_chain(self) -> List[str]:
        """安装用的镜像顺序：主镜像 → 其余镜像（去重，最多 3 个）。"""
        mirrors = list(REQUIRED_DEPENDENCIES.get("pip_mirrors") or [])
        primary = self.test_mirror_speed()
        chain = [primary] + [m for m in mirrors if m != primary]
        seen, out = set(), []
        for m in chain:
            if m and m not in seen:
                seen.add(m)
                out.append(m)
        return out[:3]

    def _install_hint(self, output: str) -> str:
        """按 pip 输出给一条可执行的修复建议（跨平台）。"""
        low = (output or "").lower()
        if "externally-managed-environment" in low:
            return "系统 Python 受 PEP 668 保护：已自动加 --break-system-packages；建议改用虚拟环境"
        if any(k in low for k in ("failed building wheel", "command 'gcc' failed",
                                  "clang: not found", "gcc: not found", "error: command")):
            if self.system_type == "Termux":
                return "缺少编译工具链 → 执行: pkg install -y clang python"
            if self.system_type in ("Linux/macOS", "SpecialLinux"):
                return "缺少编译工具链 → 执行: sudo apt install -y build-essential python3-dev"
            return "缺少 C 编译工具链"
        if "no matching distribution" in low:
            return "该平台没有预编译包（Android 无 manylinux wheel），需本地编译"
        if any(k in low for k in ("timed out", "connection", "network", "temporary failure")):
            return "网络不可达 → 检查网络或更换镜像源"
        return ""

    @timer("install_single_lib")
    def install_single_lib(self, lib_name: str, mirror: str = "") -> Tuple[bool, str]:
        """安装单个库（镜像回退 / PEP 668 自适应 / 返回失败输出）。

        返回 (是否成功, 最后一次输出)。旧实现把 stdout/stderr 全丢 DEVNULL，
        失败时用户看不到任何原因（缺编译器 / 网络不通 / PEP 668），且从不重试。
        """
        mirrors = self._mirror_chain()
        if mirror:
            mirrors = [mirror] + [m for m in mirrors if m != mirror]

        last_out = ""
        attempts = []
        for idx, m in enumerate(mirrors):
            attempts.append((m, False))        # 先用缓存（重复安装快很多）
            if idx == 0:
                attempts.append((m, True))     # 缓存疑似损坏时再禁缓存重试一次
        for m, no_cache in attempts:
            ok, out = self._pip_install_once(lib_name, m, no_cache=no_cache)
            last_out = out
            if ok:
                return True, last_out
            if "externally-managed-environment" in (out or "").lower():
                # PEP 668（Debian/Ubuntu/Kali）：必须显式放行
                ok, out = self._pip_install_once(lib_name, m, no_cache=no_cache,
                                                 extra_args=("--break-system-packages",))
                last_out = out
                if ok:
                    return True, last_out
        return False, last_out

    def _pip_install_once(self, lib_name: str, mirror: str, no_cache: bool = False,
                          extra_args: Tuple[str, ...] = ()) -> Tuple[bool, str]:
        """跑一次 pip install，返回 (是否成功, 合并输出)。"""
        pip_cmd = [self.python_exe, "-m", "pip", "install", "--prefer-binary"]
        if no_cache:
            pip_cmd.append("--no-cache-dir")
        pip_cmd += ["-i", mirror]
        pip_cmd += list(extra_args)
        pip_cmd.append(lib_name)
        try:
            result = subprocess.run(pip_cmd, check=False, capture_output=True,
                                    text=True, timeout=300)
            out = (result.stdout or "") + (result.stderr or "")
            return result.returncode == 0, out
        except subprocess.TimeoutExpired:
            return False, "pip install 超时（300s）"
        except Exception as e:
            return False, f"pip install 异常: {e}"
    ''',
)

# ── 4) 安装主循环：新接口 + 输出尾部 + 平台提示 ──
patch(
    """        for i, lib in enumerate(libs, 1):
            if show_detail:
                print(f"    [{i}/{total}] 正在安装 {lib}...")
            try:
                pip_cmd = [self.python_exe, "-m", "pip", "install",
                          "--no-cache-dir", "-i", best_mirror, lib]
                result = subprocess.run(pip_cmd, check=False,
                                       stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, timeout=120)
                if result.returncode == 0:
                    if show_detail:
                        print(f"      ✓ {lib} 安装成功")
                    succeeded.append(lib)
                else:
                    if show_detail:
                        print(f"      ✗ {lib} 安装失败")
                    failed.append(lib)
            except Exception as e:
                if show_detail:
                    print(f"      ✗ {lib} 安装异常: {e}")
                failed.append(lib)""",
    """        for i, lib in enumerate(libs, 1):
            if show_detail:
                print(f"    [{i}/{total}] 正在安装 {lib}...")
            try:
                ok, out = self.install_single_lib(lib, best_mirror)
                if ok:
                    if show_detail:
                        print(f"      ✓ {lib} 安装成功")
                    succeeded.append(lib)
                else:
                    if show_detail:
                        print(f"      ✗ {lib} 安装失败")
                        _tail = [ln for ln in (out or "").splitlines() if ln.strip()][-6:]
                        if _tail:
                            print("        ── pip 输出（尾部）──")
                            for ln in _tail:
                                print(f"        {ln}")
                        _hint = self._install_hint(out)
                        if _hint:
                            print(f"        💡 {_hint}")
                    failed.append(lib)
            except Exception as e:
                if show_detail:
                    print(f"      ✗ {lib} 安装异常: {e}")
                failed.append(lib)""",
)


def main():
    with open(MAIN, encoding="utf-8") as f:
        text = f.read()
    for old, new in P:
        n = text.count(old)
        if n != 1:
            print(f"FAIL: 命中 {n} 次\n{old[:200]}")
            return 1
        text = text.replace(old, new, 1)
    with open(MAIN, "w", encoding="utf-8") as f:
        f.write(text)
    print("PATCH_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
