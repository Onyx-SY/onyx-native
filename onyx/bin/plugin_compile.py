#!/usr/bin/env python3
"""AI Plugin C Compiler — cross-platform quick compile.
Output goes to ~/.ai_onyx_plugin/ for runtime loading.

Usage:
  python bin/plugin_compile.py source.c                        # compile to ~/.ai_onyx_plugin/
  python bin/plugin_compile.py ai_plugin/my_plugin.c -o my     # compile with custom name
  python bin/plugin_compile.py --list                          # list installed plugins
"""

import os, sys, platform, subprocess, argparse, shutil

PLUGIN_DIR = os.path.join(os.path.expanduser("~"), ".ai_onyx_plugin")
LIB_C_CODE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "c_code")


def _sys():
    s = platform.system().lower()
    return "termux" if ("termux" in sys.prefix.lower() or os.path.exists("/data/data/com.termux")) else s


def _arch():
    m = platform.machine().lower()
    return {"x86_64": "x64", "amd64": "x64", "i386": "x86", "i686": "x86",
            "aarch64": "arm64", "arm64": "arm64", "armv7l": "arm"}.get(m, m)


def _suffix():
    s = _sys()
    return ".dll" if s == "windows" else ".dylib" if s == "darwin" else ".so"


def _find_src(name: str) -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for p in (name, os.path.join(LIB_C_CODE, name), os.path.join(LIB_C_CODE, name + ".c"),
              os.path.join(os.getcwd(), name), os.path.join(os.getcwd(), name + ".c"),
              os.path.join(root, name), os.path.join(root, name + ".c")):
        if os.path.isfile(p):
            return os.path.abspath(p)
    return ""


def compile_c(source: str, output: str = "", extra_flags: list = None) -> str:
    src = _find_src(source)
    if not src:
        print(f"❌ Source not found: {source}", file=sys.stderr)
        return ""

    base = (output or os.path.splitext(os.path.basename(src))[0]).replace("_lib", "")
    os.makedirs(PLUGIN_DIR, exist_ok=True)
    out = os.path.join(PLUGIN_DIR, f"{base}{_suffix()}")

    cc = shutil.which("gcc") or shutil.which("cc") or shutil.which("clang")
    if not cc:
        if _sys() in ("linux", "termux") and shutil.which("apt"):
            subprocess.run(["apt", "install", "-y", "gcc"], capture_output=True)
            cc = shutil.which("gcc")
        if _sys() == "darwin" and shutil.which("xcode-select"):
            subprocess.run(["xcode-select", "--install"], capture_output=True)
            cc = shutil.which("clang") or shutil.which("gcc")
        if not cc:
            print("❌ No C compiler found (install gcc/clang)", file=sys.stderr)
            return ""

    flags = ["-shared", "-fPIC", "-O2"] + (extra_flags or [])
    inc = ["-I", LIB_C_CODE] if os.path.isdir(LIB_C_CODE) else []
    cmd = [cc] + inc + flags + ["-o", out, src]

    print(f"🔧 {src}")
    print(f"   -> {out}")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        print(f"❌ {r.stderr.strip()}", file=sys.stderr)
        return ""
    print(f"✅ {os.path.getsize(out)} bytes")
    return out


def list_plugins() -> list:
    if not os.path.isdir(PLUGIN_DIR):
        return []
    return sorted([f for f in os.listdir(PLUGIN_DIR) if f.endswith((".so", ".dll", ".dylib"))])


def main():
    p = argparse.ArgumentParser(description="AI Plugin C Compiler")
    p.add_argument("source", nargs="?", help=".c source file")
    p.add_argument("-o", "--output", help="Output name (without suffix)")
    p.add_argument("--flags", nargs="*", default=[], help="Extra compiler flags")
    p.add_argument("--list", action="store_true", help="List installed plugins")
    a = p.parse_args()

    if a.list:
        for f in list_plugins():
            fp = os.path.join(PLUGIN_DIR, f)
            print(f"  {f}  ({os.path.getsize(fp)} bytes)")
        return

    if not a.source:
        p.print_help()
        return

    r = compile_c(a.source, a.output, a.flags)
    if not r:
        sys.exit(1)


if __name__ == "__main__":
    main()
