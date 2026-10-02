#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证：直接读 .git 拿分支（不 fork git），可绕开 dubious-ownership。"""
import os
import sys

REPO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tmp", "gitdemo")


def find_git_dir(start):
    path = start
    while True:
        cand = os.path.join(path, ".git")
        if os.path.isdir(cand):
            return cand
        if os.path.isfile(cand):
            with open(cand, "r", encoding="utf-8", errors="ignore") as f:
                line = f.readline().strip()
            if line.startswith("gitdir:"):
                gd = line[len("gitdir:"):].strip()
                if not os.path.isabs(gd):
                    gd = os.path.normpath(os.path.join(path, gd))
                return gd if os.path.isdir(gd) else ""
            return ""
        parent = os.path.dirname(path)
        if parent == path:
            return ""
        path = parent


def read_refs(gd, kind):
    names = set()
    base = os.path.join(gd, "refs", kind)
    for root, _d, files in os.walk(base):
        for fn in files:
            names.add(os.path.relpath(os.path.join(root, fn), base).replace(os.sep, "/"))
    try:
        with open(os.path.join(gd, "packed-refs"), "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line[0] in "#^":
                    continue
                parts = line.split(" ", 1)
                pref = "refs/%s/" % kind
                if len(parts) == 2 and parts[1].startswith(pref):
                    names.add(parts[1][len(pref):])
    except OSError:
        pass
    if kind == "heads":
        try:
            with open(os.path.join(gd, "HEAD"), "r", encoding="utf-8", errors="ignore") as f:
                head = f.readline().strip()
            if head.startswith("ref: refs/heads/"):
                names.add(head[len("ref: refs/heads/"):])
        except OSError:
            pass
    return sorted(names)


os.chdir(REPO)
gd = find_git_dir(os.getcwd())
print("gitdir :", gd)
print("branches:", read_refs(gd, "heads"))
print("tags    :", read_refs(gd, "tags"))
