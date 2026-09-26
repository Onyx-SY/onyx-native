# -*- coding: utf-8 -*-
"""M4：Termux 等系统的命令/路径适配。

1. env_probe：修 `ss … | head || netstat … | head` —— 管道退出码被 head 覆盖，
   `||` 永不触发 → 缺 ss 的机器（Termux/Android）「监听端口」永远为空。
2. plugin_compile：Termux 只有 pkg（apt 是 stub）→ 按平台选包管理器。
3. get_lib_path：Termux 搜索路径漏了项目内 lib/c → HOME 被重定向时 C 库永远找不到。
4. 编译.py：Termux 无 sudo/apt → 加 pkg 分支；input() 非 TTY 时不再阻塞。
5. perm_path.json：/tmp 在 Android 不存在 → 补 Termux 临时目录等价规则。
6. Main.py：平台判定复用 lib.get_lib_path._is_termux_environment（单一事实来源）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

P = []


def patch(rel, old, new):
    P.append((os.path.join(ROOT, rel), old, new))


# ── 1) env_probe：管道退出码 ──
patch(
    "bin/ai_lib/env_probe.py",
    '''"extra": [("监听端口", "ss -tln 2>/dev/null | head -10 || netstat -tln 2>/dev/null | head -10")],''',
    '''# 注意：`cmd1 | head || cmd2 | head` 是错的 —— 管道退出码取自 head(0)，
# `||` 永不触发 → 没有 ss 的机器（Android/Termux、精简容器）永远拿不到端口列表。
# 正确写法是用 { … || …; } 让 `||` 作用在命令本身，再交给 head 截断。
        "extra": [("监听端口", "{ ss -tln 2>/dev/null || netstat -tln 2>/dev/null; } | head -10")],''',
)

patch(
    "bin/ai_lib/env_probe.py",
    '''("本地 Web 端口", "ss -tln 2>/dev/null | grep -E ':(80|443|8000|8080|3000|5000|8888|9000) ' | head -8 || netstat -tln 2>/dev/null | grep -E ':(80|443|8000|8080|3000|5000|8888|9000) ' | head -8")],''',
    '''# 同上：`||` 必须作用在命令上，否则没有 ss 时整条规则静默为空
                  ("本地 Web 端口", "{ ss -tln 2>/dev/null || netstat -tln 2>/dev/null; } | grep -E ':(80|443|8000|8080|3000|5000|8888|9000) ' | head -8")],''',
)

# ── 2) plugin_compile：包管理器按平台 ──
patch(
    "bin/plugin_compile.py",
    '''    cc = shutil.which("gcc") or shutil.which("cc") or shutil.which("clang")
    if not cc:
        if _sys() in ("linux", "termux") and shutil.which("apt"):
            subprocess.run(["apt", "install", "-y", "gcc"], capture_output=True)
            cc = shutil.which("gcc")''',
    '''    cc = shutil.which("gcc") or shutil.which("cc") or shutil.which("clang")
    if not cc:
        # Termux/Android 上只有 `pkg`（`apt` 是 stub，且无 root/sudo）→ 必须分开处理，
        # 旧实现统一跑 `apt install -y gcc` 在 Termux 上是静默失败。
        if _sys() == "termux" or shutil.which("pkg"):
            subprocess.run(["pkg", "install", "-y", "clang"], capture_output=True)
            cc = shutil.which("clang") or shutil.which("gcc") or shutil.which("cc")
        if not cc and _sys() in ("linux", "termux") and shutil.which("apt"):
            _sudo = [] if (os.geteuid() == 0 if hasattr(os, "geteuid") else False) else (["sudo"] if shutil.which("sudo") else [])
            if _sudo or not shutil.which("sudo"):
                subprocess.run(_sudo + ["apt", "install", "-y", "gcc"], capture_output=True)
                cc = shutil.which("gcc")''',
)

# ── 3) get_lib_path：Termux 也要搜项目内 lib/c ──
patch(
    "lib/get_lib_path.py",
    '''def _get_termux_search_paths(lib_name: str, lib_filename: str) -> List[str]:
    """Termux 环境搜索路径"""
    return [
        os.path.join(TERMUX_HOME, "c", lib_name, lib_filename),
        os.path.join(TERMUX_HOME, lib_name, lib_filename),
        os.path.join(TERMUX_PREFIX, "lib", lib_filename),
        os.path.join(TERMUX_PREFIX, "local", "lib", lib_filename),
    ]''',
    '''def _get_termux_search_paths(lib_name: str, lib_filename: str) -> List[str]:
    """Termux 环境搜索路径。

    2026-09 修复：旧实现只搜 Termux home / $PREFIX/lib，**完全不搜项目内 lib/c** ——
    而仓库自带 lib/c/<name>/<arch>.so。项目把库拷到 `expanduser("~")/c` 依赖 HOME
    未被重定向；一旦 HOME 指向别处（沙箱/自定义 HOME），拷贝目标与查找目标不一致，
    C 库就永远找不到。这里把项目路径放在最前，与 Linux 分支保持一致。
    """
    return [
        # 项目目录（最可信来源，优先）
        os.path.join(_PROJECT_ROOT, "lib", "c", lib_name, lib_filename),
        os.path.join(_PROJECT_ROOT, "c", lib_name, lib_filename),
        os.path.join(_SCRIPT_DIR, "c", lib_name, lib_filename),
        # Termux 运行时拷贝目录
        os.path.join(TERMUX_HOME, "c", lib_name, lib_filename),
        os.path.join(TERMUX_HOME, lib_name, lib_filename),
        os.path.join(TERMUX_PREFIX, "lib", lib_filename),
        os.path.join(TERMUX_PREFIX, "local", "lib", lib_filename),
    ]''',
)

# ── 4) 编译.py：Termux 分支 + 非交互 ──
patch(
    "lib/c_code/编译.py",
    '''    def _install_compiler_linux(self, compiler_type, target_arch=None, target_system=None):
        """在Linux上安装编译器"""
        if shutil.which('apt'):''',
    '''    def _install_compiler_linux(self, compiler_type, target_arch=None, target_system=None):
        """在Linux上安装编译器"""
        # Termux/Android：没有 sudo，`apt` 只是 stub，必须用 pkg；
        # 而且 clang 才是官方工具链（gcc 通常由 clang 提供）。
        if 'termux' in (sys.prefix or '').lower() or shutil.which('pkg'):
            print("✓ 检测到 Termux 环境，使用 pkg")
            try:
                subprocess.run(['pkg', 'install', '-y', 'clang', 'make'],
                               check=True, timeout=600)
                print("\\n✓ clang 编译器安装成功!")
                return True
            except Exception as e:
                print(f"✗ 安装失败: {e}")
                print("  可手动执行: pkg install -y clang make")
                return False

        if shutil.which('apt'):''',
)

patch(
    "lib/c_code/编译.py",
    '''        print(f"\\n将安装以下包: {', '.join(packages)}")
        confirm = input("是否继续安装？(y/N): ").strip().lower()
        if confirm != 'y':
            print("已取消安装")
            return False''',
    '''        print(f"\\n将安装以下包: {', '.join(packages)}")
        # 非交互环境（管道/后台/自动化）下 input() 会永久阻塞 → 只在真正的 TTY 里询问，
        # 否则默认继续（安装编译器是用户显式发起的操作）。
        if sys.stdin is not None and sys.stdin.isatty():
            confirm = input("是否继续安装？(y/N): ").strip().lower()
            if confirm != 'y':
                print("已取消安装")
                return False
        else:
            print("（非交互环境，直接继续安装）")''',
)

# ── 5) perm_path.json：Termux 临时目录等价规则 ──
patch(
    "etc/perm_path.json",
    '''  "/tmp/<*:10>": {
    "mode": "blacklist",
    "min_mode": "adv",
    "allow_advanced_syntax": true,
    "commands": [
      "mkfs", "fdisk", "parted", "gdisk", "sgdisk",
      "dd", "dcfldd",
      "wipefs", "shred",
      "mkswap", "cryptsetup",
      "pvremove", "pvcreate", "vgremove", "vgcreate", "lvremove", "lvcreate",
      "mdadm", "dmsetup"
    ]
  },''',
    '''  "/tmp/<*:10>": {
    "mode": "blacklist",
    "min_mode": "adv",
    "allow_advanced_syntax": true,
    "commands": [
      "mkfs", "fdisk", "parted", "gdisk", "sgdisk",
      "dd", "dcfldd",
      "wipefs", "shred",
      "mkswap", "cryptsetup",
      "pvremove", "pvcreate", "vgremove", "vgcreate", "lvremove", "lvcreate",
      "mdadm", "dmsetup"
    ]
  },

  "/data/data/com.termux/files/usr/tmp/<*:10>": {
    "mode": "blacklist",
    "min_mode": "adv",
    "allow_advanced_syntax": true,
    "commands": [
      "mkfs", "fdisk", "parted", "gdisk", "sgdisk",
      "dd", "dcfldd",
      "wipefs", "shred",
      "mkswap", "cryptsetup",
      "pvremove", "pvcreate", "vgremove", "vgcreate", "lvremove", "lvcreate",
      "mdadm", "dmsetup"
    ]
  },''',
)

# ── 6) Main.py：平台判定复用单一实现 ──
patch(
    "Main.py",
    '''    @timer("detect_system")
    def detect_system(self) -> str:
        """检测系统类型"""
        is_termux = "termux" in sys.prefix.lower() or \\
                    (os.path.exists("/data/data/com.termux") if hasattr(os, 'path') else False)''',
    '''    @timer("detect_system")
    def detect_system(self) -> str:
        """检测系统类型。

        优先复用 lib.get_lib_path._is_termux_environment（全仓单一事实来源，看
        Termux home/$PREFIX 是否同时存在 + sys.prefix），失败再退回本地判据 ——
        避免「同一个系统在不同模块被判成不同平台」。
        """
        is_termux = False
        try:
            from lib.get_lib_path import _is_termux_environment
            is_termux = bool(_is_termux_environment())
        except Exception:
            is_termux = "termux" in sys.prefix.lower() or \\
                        (os.path.exists("/data/data/com.termux") if hasattr(os, 'path') else False)''',
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
            print(f"FAIL {os.path.relpath(path, ROOT)}: 命中 {n} 次\n{old[:200]}")
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
