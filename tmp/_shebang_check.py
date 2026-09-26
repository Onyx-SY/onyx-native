# -*- coding: utf-8 -*-
"""shebang 优先 + 内部入口放行 快速验证。"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib.parse import read_shebang, _shebang_command, handle_executable_path  # noqa
import lib.safe as safe  # noqa

d = tempfile.mkdtemp(prefix="onyx_shebang_")


def w(name, content, mode=0o644):
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    os.chmod(p, mode)
    return p


sh_exec = w("a_exec.sh", "#!/bin/sh\necho hi\n", 0o755)
sh_noexec = w("a_noexec.sh", "#!/bin/sh\necho hi\n", 0o644)
env_py = w("b.py", "#!/usr/bin/env python3\nprint(1)\n", 0o644)
env_s = w("c.py", "#!/usr/bin/env -S python3 -u\nprint(1)\n", 0o644)
no_sb = w("d.sh", "echo hi\nls\n", 0o644)
notscript = w("e.txt", "hello\n", 0o644)

print("read_shebang:")
for p in (sh_exec, env_py, env_s, no_sb, notscript):
    print("   ", os.path.basename(p), "->", repr(read_shebang(p)))

print("_shebang_command:")
for body in ("/bin/sh", "/usr/bin/env python3", "/usr/bin/env -S python3 -u",
             "/usr/bin/env -u FOO python3", "/bin/bash -e"):
    print("   ", repr(body), "->", _shebang_command(body))

print("handle_executable_path（resolver 把相对名映射到临时目录）:")
def resolve(tok):
    return os.path.join(d, os.path.basename(tok))
for p in (sh_exec, sh_noexec, env_py, env_s, no_sb):
    out = handle_executable_path("./" + os.path.basename(p), resolve, "/ROOT")
    print("   ", os.path.basename(p), "->", out)

print("无 shebang 的自执行改写（ROOT_DIR=/ROOT）:")
out = handle_executable_path("./d.sh", resolve, "/ROOT")
assert out == f'python /ROOT/onyx/cmd.py -c "source ./d.sh"', out

print("\n内部入口豁免：")
safe._ROOT_DIR = "/ROOT"
entries = safe._onyx_internal_entry_paths()
print("   entries =", entries)
assert "/ROOT/onyx/cmd.py" in entries

safe.PERM_PATH_CONFIG = [{"pattern": "/ROOT", "fixed_phys": "/ROOT", "mode": "blacklist",
                          "allowed": ["python"], "min_mode": "adv"}]
class M:
    current_mode = "mid"
import io, contextlib
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    ok = safe.check_path_permission_for_cmd("python", ["/ROOT/onyx/cmd.py"], "u0", M(),
                                            user_home=d)
print("   仅 cmd.py 路径 ->", ok, "（应为 True：内部入口被跳过）", "| 输出:", buf.getvalue().strip()[:60])

buf2 = io.StringIO()
with contextlib.redirect_stdout(buf2):
    ok2 = safe.check_path_permission_for_cmd("python", ["/ROOT/onyx/other.py"], "u0", M(),
                                             user_home=d)
print("   普通路径       ->", ok2, "（应为 False：仍被 min_mode 拦）")
assert ok is True and ok2 is False
print("\nOK")
