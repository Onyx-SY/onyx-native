# -*- coding: utf-8 -*-
"""step-1 补 2：`input_lib` 把 HAS_PYGMENTS 用 `from ... import` 钉死成 False。

`from .mul_line import HAS_PYGMENTS` 取的是**导入那一刻的值**（那时 pygments 还没懒加载
→ False），之后 mul_line 加载成功也不会同步 → `input_lib.py:1168` 的多行键位判断
永远走降级分支。改为动态读取 + PEP562 兼容转发。
"""
import io
import sys

P = "lib/terminal/input_lib.py"
s = io.open(P, encoding="utf-8").read()


def rep(old, new, tag):
    global s
    n = s.count(old)
    if n != 1:
        print(f"❌ {tag}: 命中 {n} 次")
        sys.exit(1)
    s = s.replace(old, new)
    print(f"✅ {tag}")


rep('''    SyntaxType,
    SmartSyntaxDetector,
    HAS_PYGMENTS,
)
''',
    '''    SyntaxType,
    SmartSyntaxDetector,
)
from . import mul_line as _mul_line


def _has_pygments() -> bool:
    """pygments 是否已就绪（运行时开关，必须**动态**读）。

    ⚠️ 不要用 `from .mul_line import HAS_PYGMENTS`：那会把「导入那一刻」的值
    （懒加载尚未触发 → False）永久钉死在本模块，之后 mul_line 加载成功也不会同步，
    多行输入的键位/高亮判断就永远走降级分支。
    """
    return bool(getattr(_mul_line, "HAS_PYGMENTS", False))


def __getattr__(name):
    """兼容外部 `input_lib.HAS_PYGMENTS` 写法（动态转发到 mul_line）。"""
    if name == "HAS_PYGMENTS":
        return _has_pygments()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
''',
    "import → 动态访问器")

rep('''                key_bindings=ml_input.kb if HAS_PYGMENTS else kb,''',
    '''                key_bindings=ml_input.kb if _has_pygments() else kb,''',
    "1168 动态判断")

io.open(P, "w", encoding="utf-8").write(s)
print("written")
