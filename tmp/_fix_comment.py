# -*- coding: utf-8 -*-
"""修 remove_comments：`#` 只有在「词首」才是注释。

bash 规则：`echo a#b` 里的 `#` 是普通字符；`echo a # b` / 行首 `#x` 才是注释。
原实现遇到任何引号外的 `#` 就截断 → `echo a#b` 被吃成 `echo a`。
"""
import shutil

P = 'lib/parse.py'
s = open(P, encoding='utf-8').read()

OLD = """                if ch == '#' and not in_single and not in_double:
                    # 检查是否在 here-doc 中
                    if not is_in_heredoc_content(text, i):
                        # 遇到注释，直接结束这一行
                        break"""
NEW = """                if ch == '#' and not in_single and not in_double:
                    # bash：`#` 只有在「词首」（行首或前面是空白）才是注释，
                    # `a#b` 里的 # 是普通字符 → 必须看前一个已处理字符。
                    _prev = new_line[-1] if new_line else ''
                    if _prev == '' or _prev.isspace():
                        # 检查是否在 here-doc 中
                        if not is_in_heredoc_content(text, i):
                            # 遇到注释，直接结束这一行
                            break"""

n = s.count(OLD)
assert n == 1, ('occurrences', n)
shutil.copy(P, P + '.bak4')
open(P, 'w', encoding='utf-8').write(s.replace(OLD, NEW, 1))
print('COMMENT FIX APPLIED')
