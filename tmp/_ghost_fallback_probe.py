# -*- coding: utf-8 -*-
"""直接验证 SmartAutoSuggest 的回退：无历史命中时用补全首项作虚影。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from prompt_toolkit.document import Document
from lib.terminal.com import SmartCompleter, SmartAutoSuggest


class FakeBuffer:
    complete_state = None


def main():
    cmds = ["ls", "lsblk", "lsof", "echo", "git", "grep", "cat"]
    sc = SmartCompleter(cmds, history_buffer=["echo hello-world", "git status"], user_home_dir="")
    sug = SmartAutoSuggest(sc)

    for text in ["l", "ec", "ech", "xyz", "git s"]:
        doc = Document(text=text, cursor_position=len(text))
        comps = [c.text for c in sc.get_completions(doc, None)]
        r = sug.get_suggestion(FakeBuffer(), doc)
        print(f"text={text!r:10} completions[:3]={comps[:3]}  ghost={getattr(r, 'text', None)!r}")

    # 历史命中仍优先
    doc = Document(text="git s", cursor_position=5)
    print("git s →", repr(sug.get_suggestion(FakeBuffer(), doc)))


if __name__ == "__main__":
    main()
