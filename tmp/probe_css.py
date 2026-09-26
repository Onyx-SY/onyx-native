# -*- coding: utf-8 -*-
import io
src = io.open("bin/ai_tui.py", encoding="utf-8").read()
i = src.index("hist-list { max-height")
print(repr(src[i - 10:i + 120]))
print("has CRLF:", "\r\n" in src)
