# cmd_com/builtin.py
"""
Onyx 内置动态命令补全集合（30 个）。

设计原则：
    - 每个补全器都走 cached()，避免频繁 subprocess 造成卡顿
    - 外部命令不存在时静默降级为静态候选（不报错、不阻塞）
    - git 的分支/标签/remote 直接读 .git（不 fork），绕开 Termux
      共享存储上的「dubious ownership」报错，也省掉每次补全的开销
    - 命令覆盖：git / docker / kubectl / npm / pip / cargo / go /
               make / systemctl / ssh / tmux / aws / terraform /
               ansible / helm / composer / mvn / gradle / dotnet /
               conda / poetry / yarn / pnpm / pytest / rake /
               bundle / jupyter / hugo / ffmpeg / gcloud

如果你要自己扩展：
    - 在 ~/.cmd_com/ 下新建 xxx.py
    - 实现 register(reg) 或 COMMANDS = {...}
    - 见 dynamic_cmd.py 顶部文档
"""

import os
from typing import Iterable, List

from lib.terminal.dynamic_cmd import (
    CompletionContext, CompletionItem, cached, run_command, prefix_match,
)


# ============================================================
# 工具
# ============================================================

def _emit(items: Iterable[str], current: str, meta: str = "",
          style: str = "") -> Iterable[CompletionItem]:
    for it in items:
        if it and prefix_match(current, it):
            yield CompletionItem(text=it, meta=meta, style=style)


def _subcommands(names: List[str], meta: str = "subcmd",
                 style: str = "ansiyellow"):
    """数据驱动的子命令补全器工厂。"""
    names = list(names)

    def _completer(ctx: CompletionContext):
        yield from _emit(names, ctx.current, meta, style)

    return _completer


def _static(spec: dict):
    """{subcmd: [args...], "_opts": [...], "_when_arg0": {...}}"""
    def _completer(ctx: CompletionContext):
        current = ctx.current
        opts = spec.get("_opts", [])
        if current.startswith('-'):
            yield from _emit(opts, current, "option", "ansired")
            return
        if not ctx.args:
            yield from _emit(spec.get("_subs", []), current,
                             "subcmd", "ansiyellow")
            return
        sub = ctx.args[0]
        table = spec.get(sub)
        if table is None:
            table = spec.get("_default", [])
        yield from _emit(table, current, "arg", "ansimagenta")
        yield from _emit(opts, current, "option", "ansired")
    return _completer


# ============================================================
# 1. git
# ============================================================

_GIT_SUBS = [
    "add", "am", "apply", "archive", "bisect", "blame", "branch",
    "bundle", "checkout", "cherry-pick", "clean", "clone", "commit",
    "config", "describe", "diff", "fetch", "format-patch", "gc",
    "grep", "init", "log", "merge", "mv", "notes", "pull", "push",
    "rebase", "reflog", "remote", "reset", "restore", "revert", "rm",
    "shortlog", "show", "stash", "status", "submodule", "switch",
    "tag", "worktree",
]

_GIT_BRANCH_SUBS = {
    "checkout", "switch", "merge", "rebase", "diff", "log", "reset",
    "cherry-pick", "show", "branch",
}
_GIT_REMOTE_SUBS = {"push", "pull", "fetch", "remote", "clone"}


def _find_git_dir() -> str:
    """从 cwd 向上查找 .git 目录（兼容 worktree / submodule 的 gitdir 文件）。

    不在仓库里时返回 ""。
    """
    try:
        path = os.getcwd()
    except Exception:
        return ""
    while True:
        cand = os.path.join(path, ".git")
        if os.path.isdir(cand):
            return cand
        if os.path.isfile(cand):        # worktree / submodule：文件内容形如 "gitdir: <path>"
            try:
                with open(cand, "r", encoding="utf-8", errors="ignore") as f:
                    line = f.readline().strip()
            except OSError:
                return ""
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


def _read_git_refs(kind: str) -> List[str]:
    """直接读 .git 里的引用名（kind = "heads" / "tags"），不 fork git 子进程。

    为什么要绕开 git 命令：Android/Termux 上仓库多位于 /storage 共享存储，
    目录 owner 与进程 uid 不一致，git 会以「dubious ownership」直接退出
    （rc=128），补全于是静默拿不到任何分支/标签。
    Onyx 提示符里的分支名正是直接读 .git/HEAD 得到的（core/display.py），
    这里沿用同一思路，顺带省掉每次补全的 fork 开销。
    """
    git_dir = _find_git_dir()
    if not git_dir:
        return []

    prefix = "refs/%s/" % kind
    names = set()

    # 1) 松散引用：refs/<kind>/**（分支名可含 /，需递归）
    base = os.path.join(git_dir, "refs", kind)
    for root, _dirs, files in os.walk(base):
        for fn in files:
            full = os.path.join(root, fn)
            names.add(os.path.relpath(full, base).replace(os.sep, "/"))

    # 2) 打包引用：packed-refs（行格式 "<sha> refs/heads/xxx"）
    try:
        with open(os.path.join(git_dir, "packed-refs"), "r",
                  encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line[0] in "#^":
                    continue
                parts = line.split(" ", 1)
                if len(parts) == 2 and parts[1].startswith(prefix):
                    names.add(parts[1][len(prefix):])
    except OSError:
        pass

    # 3) 尚无 commit 的仓库（unborn HEAD）没有 refs/heads/*，从 HEAD 补
    if kind == "heads":
        try:
            with open(os.path.join(git_dir, "HEAD"), "r",
                      encoding="utf-8", errors="ignore") as f:
                head = f.readline().strip()
            if head.startswith("ref: refs/heads/"):
                names.add(head[len("ref: refs/heads/"):])
        except OSError:
            pass

    return sorted(names)


def _read_git_remotes() -> List[str]:
    """直接解析 .git/config 的 [remote "xxx"] 段，不 fork git。"""
    git_dir = _find_git_dir()
    if not git_dir:
        return []
    names: List[str] = []
    try:
        with open(os.path.join(git_dir, "config"), "r",
                  encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line.startswith("[remote "):
                    continue
                name = line[len("[remote "):].rstrip("]").strip().strip('"')
                if name and name not in names:
                    names.append(name)
    except OSError:
        return []
    return names


def _git_cli(args: List[str]) -> List[str]:
    """git 命令兜底：带 -c safe.directory=*，避免共享存储上的 ownership 误报。"""
    return run_command(["git", "-c", "safe.directory=*"] + args)


def _git_cached(name: str, ttl: float, direct, cli_args: List[str]) -> List[str]:
    """直读 .git 优先，拿不到再退回 git 命令；缓存 key 带上 cwd（cd 后不串味）。"""
    try:
        cwd = os.getcwd()
    except Exception:
        cwd = ""
    return cached("%s@%s" % (name, cwd), ttl,
                  lambda: direct() or _git_cli(cli_args))


def _git_branches() -> List[str]:
    return _git_cached("git_branches", 3.0,
                       lambda: _read_git_refs("heads"),
                       ["branch", "--format=%(refname:short)"])


def _git_tags() -> List[str]:
    return _git_cached("git_tags", 5.0,
                       lambda: _read_git_refs("tags"),
                       ["tag"])


def _git_remotes() -> List[str]:
    return _git_cached("git_remotes", 5.0,
                       _read_git_remotes,
                       ["remote"])


def git_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_GIT_SUBS, ctx.current, "subcmd", "ansiyellow")
        return

    sub = ctx.args[0]

    if sub == "stash":
        yield from _emit(
            ["list", "show", "pop", "apply", "drop", "clear", "push", "save"],
            ctx.current, "action", "ansiyellow")
        return

    if sub == "remote":
        yield from _emit(
            ["add", "remove", "rename", "set-url", "show", "prune",
             "get-url", "set-head"],
            ctx.current, "action", "ansiyellow")
        # 第二参数给 remote 名
        if len(ctx.args) >= 2:
            yield from _emit(_git_remotes(), ctx.current, "remote", "ansicyan")
        return

    if sub == "config":
        yield from _emit(
            ["--global", "--local", "--system", "--list",
             "--get", "--set", "--unset", "--edit"],
            ctx.current, "option", "ansired")
        return

    if sub in _GIT_BRANCH_SUBS:
        for b in _git_branches():
            if prefix_match(ctx.current, b):
                yield CompletionItem(text=b, meta="branch", style="ansicyan")
        if sub in ("checkout", "switch", "tag", "merge", "rebase"):
            for t in _git_tags():
                if prefix_match(ctx.current, t):
                    yield CompletionItem(text=t, meta="tag", style="ansimagenta")
        return

    if sub in _GIT_REMOTE_SUBS:
        if len(ctx.args) == 1:
            yield from _emit(_git_remotes(), ctx.current, "remote", "ansicyan")
        elif sub == "push" and len(ctx.args) >= 2:
            # push <remote> <branch>
            for b in _git_branches():
                if prefix_match(ctx.current, b):
                    yield CompletionItem(text=b, meta="branch", style="ansicyan")
        elif sub == "pull" and len(ctx.args) >= 2:
            for b in _git_branches():
                if prefix_match(ctx.current, b):
                    yield CompletionItem(text=b, meta="branch", style="ansicyan")
        return

    # 其它子命令，兜底选项
    yield from _emit(
        ["--help", "--verbose", "--quiet", "-v", "-q"],
        ctx.current, "option", "ansired")


# ============================================================
# 2. docker
# ============================================================

_DOCKER_SUBS = [
    "attach", "build", "commit", "cp", "create", "diff", "events",
    "exec", "export", "history", "images", "import", "info", "inspect",
    "kill", "load", "login", "logout", "logs", "network", "pause",
    "port", "ps", "pull", "push", "rename", "restart", "rm", "rmi",
    "run", "save", "search", "start", "stats", "stop", "tag", "top",
    "unpause", "update", "version", "volume", "wait",
]

_DOCKER_CONTAINER_SUBS = {
    "exec", "stop", "start", "kill", "logs", "inspect",
    "restart", "rm", "attach", "top", "pause", "unpause", "port",
    "rename", "stats", "wait", "diff", "export", "commit", "cp",
}
_DOCKER_IMAGE_SUBS = {
    "rmi", "run", "pull", "push", "tag", "history", "save", "inspect",
}


def _docker_containers(running_only: bool = True) -> List[str]:
    key = "docker_ps" if running_only else "docker_ps_all"
    cmd = ["docker", "ps", "--format", "{{.Names}}"]
    if not running_only:
        cmd.insert(2, "-a")
    return cached(key, 3.0, lambda: run_command(cmd))


def _docker_images() -> List[str]:
    return cached("docker_images", 5.0, lambda: run_command(
        ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"]))


def docker_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_DOCKER_SUBS, ctx.current, "subcmd", "ansiyellow")
        return

    sub = ctx.args[0]

    if sub == "network":
        yield from _emit(
            ["create", "ls", "rm", "connect", "disconnect", "inspect", "prune"],
            ctx.current, "action", "ansiyellow")
        return

    if sub == "volume":
        yield from _emit(
            ["create", "ls", "rm", "inspect", "prune"],
            ctx.current, "action", "ansiyellow")
        return

    if sub in _DOCKER_CONTAINER_SUBS:
        yield from _emit(_docker_containers(True), ctx.current,
                         "container", "ansicyan")
        yield from _emit(_docker_containers(False), ctx.current,
                         "container", "ansiblue")
        return

    if sub in _DOCKER_IMAGE_SUBS:
        yield from _emit(_docker_images(), ctx.current,
                         "image", "ansimagenta")
        return

    yield from _emit(
        ["--help", "-h", "--format", "--filter"],
        ctx.current, "option", "ansired")


# ============================================================
# 3. kubectl
# ============================================================

_KUBECTL_SUBS = [
    "apply", "attach", "auth", "autoscale", "certificate", "cluster-info",
    "completion", "config", "cordon", "cp", "create", "delete", "describe",
    "diff", "drain", "edit", "exec", "explain", "expose", "get", "label",
    "logs", "patch", "plugin", "port-forward", "proxy", "replace", "rollout",
    "run", "scale", "set", "taint", "top", "uncordon", "version",
]

_KUBECTL_RESOURCES = [
    "pods", "deployments", "services", "replicasets", "statefulsets",
    "daemonsets", "jobs", "cronjobs", "configmaps", "secrets",
    "namespaces", "nodes", "events", "ingresses", "persistentvolumes",
    "persistentvolumeclaims", "serviceaccounts", "endpoints",
]

_KUBECTL_GET_VERBS = ["get", "describe", "delete", "edit", "logs", "exec"]


def _kubectl_namespaces() -> List[str]:
    return cached("kube_ns", 5.0, lambda: [
        line.split()[0]
        for line in run_command(["kubectl", "get", "ns",
                                 "--no-headers",
                                 "-o", "custom-columns=:metadata.name"])
        if line.strip()
    ])


def kubectl_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_KUBECTL_SUBS, ctx.current, "subcmd", "ansiyellow")
        return

    sub = ctx.args[0]

    if sub == "config":
        yield from _emit(
            ["current-context", "get-contexts", "use-context",
             "view", "set", "unset", "rename-context", "delete-context",
             "set-context", "set-cluster", "set-credentials"],
            ctx.current, "action", "ansiyellow")
        return

    if sub == "rollout":
        yield from _emit(
            ["status", "history", "undo", "restart", "pause", "resume"],
            ctx.current, "action", "ansiyellow")
        return

    if sub in _KUBECTL_GET_VERBS and len(ctx.args) == 1:
        yield from _emit(_KUBECTL_RESOURCES, ctx.current,
                         "resource", "ansimagenta")
        return

    if sub in _KUBECTL_GET_VERBS and len(ctx.args) >= 2:
        # 补具体资源名
        res = ctx.args[1]
        names = cached(f"kube_{res}", 3.0, lambda: run_command(
            ["kubectl", "get", res, "--no-headers",
             "-o", "custom-columns=:metadata.name"]))
        yield from _emit(names, ctx.current, "object", "ansicyan")
        return

    yield from _emit(
        ["-n", "--namespace", "-A", "--all-namespaces",
         "-o", "--output", "-f", "--filename"],
        ctx.current, "option", "ansired")


# ============================================================
# 4. npm
# ============================================================

_NPM_SUBS = [
    "install", "i", "uninstall", "update", "run", "test", "start",
    "build", "publish", "pack", "link", "init", "list", "ls", "outdated",
    "audit", "fund", "config", "cache", "ci", "dedupe", "doctor",
    "explain", "exec", "help", "login", "logout", "ping", "prefix",
    "prune", "repo", "restart", "root", "search", "stop", "team",
    "token", "unpublish", "version", "view", "whoami",
]


def _npm_scripts() -> List[str]:
    def _read():
        import json
        try:
            with open(os.path.join(os.getcwd(), "package.json"),
                      "r", encoding="utf-8") as f:
                data = json.load(f)
            return list((data.get("scripts") or {}).keys())
        except Exception:
            return []
    return cached("npm_scripts", 5.0, _read)


def npm_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_NPM_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    sub = ctx.args[0]
    if sub in ("run", "run-script"):
        yield from _emit(_npm_scripts(), ctx.current, "script", "ansimagenta")
        return
    if sub in ("install", "i", "add", "uninstall", "remove"):
        yield from _emit(
            ["--save", "--save-dev", "--save-peer",
             "--save-optional", "--no-save", "-g", "--global",
             "--registry", "--prefix"],
            ctx.current, "option", "ansired")
        return
    yield from _emit(["--help", "-h"], ctx.current, "option", "ansired")


# ============================================================
# 5. pip
# ============================================================

_PIP_SUBS = [
    "install", "uninstall", "freeze", "list", "show", "search",
    "download", "wheel", "hash", "completion", "help", "check",
    "config", "cache", "debug", "index", "inspect",
]


def pip_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_PIP_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    sub = ctx.args[0]
    if sub in ("install", "download", "wheel"):
        # 提示包名（不联网，只列本地已装？改走最常见选项）
        yield from _emit(
            ["-r", "--requirement", "-U", "--upgrade",
             "--user", "--pre", "--no-deps",
             "-i", "--index-url", "--extra-index-url",
             "--no-cache-dir", "-t", "--target"],
            ctx.current, "option", "ansired")
        return
    yield from _emit(["--help", "-h"], ctx.current, "option", "ansired")


# ============================================================
# 6. cargo
# ============================================================

_CARGO_SUBS = [
    "build", "check", "clean", "doc", "new", "init", "add", "remove",
    "run", "test", "bench", "update", "search", "publish", "install",
    "uninstall", "login", "logout", "package", "fetch", "fix", "fmt",
    "clippy", "metadata", "tree", "vendor", "verify-project", "version",
    "yank", "generate-lockfile", "locate-project", "rustc", "rustdoc",
]


def cargo_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_CARGO_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    yield from _emit(
        ["--release", "--debug", "--target", "--features",
         "--all-features", "--no-default-features", "-p", "--package",
         "--manifest-path", "--verbose", "-v", "--quiet", "-q"],
        ctx.current, "option", "ansired")


# ============================================================
# 7. go
# ============================================================

_GO_SUBS = [
    "build", "clean", "doc", "env", "bug", "fix", "fmt", "generate",
    "get", "install", "list", "mod", "work", "run", "test", "tool",
    "version", "vet",
]


def _go_packages() -> List[str]:
    return cached("go_pkgs", 10.0, lambda: run_command(["go", "list", "./..."]))


def go_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_GO_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    sub = ctx.args[0]
    if sub in ("run", "test", "build", "install", "vet"):
        yield from _emit(_go_packages(), ctx.current, "pkg", "ansimagenta")
        yield from _emit(["-v", "-race", "-tags", "-o"], ctx.current,
                         "option", "ansired")
        return
    if sub == "mod":
        yield from _emit(
            ["init", "tidy", "download", "edit", "graph", "verify", "why"],
            ctx.current, "action", "ansiyellow")
        return
    if sub == "env":
        yield from _emit(
            ["GOPATH", "GOROOT", "GOBIN", "GOOS", "GOARCH", "GOPROXY"],
            ctx.current, "var", "ansicyan")
        return
    yield from _emit(["-h", "--help"], ctx.current, "option", "ansired")


# ============================================================
# 8. make
# ============================================================

def _make_targets() -> List[str]:
    def _read():
        targets = []
        for fname in ("Makefile", "makefile", "GNUmakefile"):
            path = os.path.join(os.getcwd(), fname)
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        if line.startswith(("\t", "#")) or "=" in line:
                            continue
                        m = line.split(":", 1)
                        if len(m) == 2 and m[0].strip():
                            targets.append(m[0].strip())
            except Exception:
                pass
            break
        return list(dict.fromkeys(targets))
    return cached("make_targets", 3.0, _read)


def make_completer(ctx: CompletionContext):
    if ctx.current.startswith('-'):
        yield from _emit(
            ["-f", "-C", "-j", "-n", "-k", "-s", "-B", "--always-make",
             "--dry-run", "--jobs", "--directory"],
            ctx.current, "option", "ansired")
        return
    yield from _emit(_make_targets(), ctx.current, "target", "ansimagenta")


# ============================================================
# 9. systemctl
# ============================================================

_SYSTEMCTL_SUBS = [
    "start", "stop", "restart", "reload", "status", "enable", "disable",
    "is-active", "is-enabled", "daemon-reload", "list-units",
    "list-unit-files", "list-sockets", "list-timers", "show", "cat",
    "edit", "mask", "unmask", "reset-failed", "kill", "isolate",
    "poweroff", "reboot", "suspend", "hibernate",
]


def _systemctl_units() -> List[str]:
    return cached("systemctl_units", 10.0, lambda: run_command(
        ["systemctl", "list-units", "--no-legend",
         "--no-pager", "--plain"]))


def systemctl_completer(ctx: CompletionContext):
    if not ctx.args or ctx.args[0].startswith('-'):
        yield from _emit(_SYSTEMCTL_SUBS, ctx.current, "subcmd", "ansiyellow")
        if ctx.current.startswith('-'):
            yield from _emit(
                ["--user", "--system", "--type", "--all", "--state",
                 "--no-pager", "--no-legend", "--plain"],
                ctx.current, "option", "ansired")
        return
    sub = ctx.args[0]
    if sub in ("start", "stop", "restart", "reload", "status", "enable",
               "disable", "is-active", "is-enabled", "cat", "show", "edit",
               "mask", "unmask", "kill"):
        units = _systemctl_units()
        unit_names = [u.split()[0] for u in units if u.split()]
        yield from _emit(unit_names, ctx.current, "unit", "ansimagenta")
        return


# ============================================================
# 10. ssh
# ============================================================

def _ssh_hosts() -> List[str]:
    def _read():
        hosts = []
        for path in (
            os.path.expanduser("~/.ssh/config"),
            os.path.expanduser("~/.ssh/known_hosts"),
        ):
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        m = line.split()
                        if not m:
                            continue
                        if line.lower().startswith("host "):
                            if len(m) >= 2:
                                hosts.append(m[1])
                        else:
                            h = m[0].split(",")[0]
                            if "|" not in h:
                                hosts.append(h)
            except Exception:
                pass
        return list(dict.fromkeys(hosts))
    return cached("ssh_hosts", 30.0, _read)


def ssh_completer(ctx: CompletionContext):
    if ctx.current.startswith('-'):
        yield from _emit(
            ["-p", "-i", "-L", "-R", "-D", "-o", "-N", "-f", "-v", "-X", "-Y"],
            ctx.current, "option", "ansired")
        return
    if len(ctx.args) == 0:
        yield from _emit(_ssh_hosts(), ctx.current, "host", "ansicyan")
        return


# ============================================================
# 11. tmux
# ============================================================

_TMUX_SUBS = [
    "attach", "attach-session", "bind", "break-pane", "capture-pane",
    "choose-tree", "clear-history", "copy-mode", "detach", "display",
    "display-message", "has-session", "kill-pane", "kill-server",
    "kill-session", "kill-window", "link-window", "list-buffers",
    "list-clients", "list-commands", "list-keys", "list-panes",
    "list-sessions", "list-windows", "load-buffer", "lock-client",
    "lock-server", "lock-session", "move-pane", "move-window", "new",
    "new-session", "new-window", "next-layout", "next-window",
    "paste-buffer", "pipe-pane", "previous-layout", "previous-window",
    "rename-session", "rename-window", "resize-pane", "respawn-pane",
    "respawn-window", "rotate-window", "save-buffer", "select-layout",
    "select-pane", "select-window", "send-keys", "set", "set-buffer",
    "set-environment", "set-option", "set-window-option", "show",
    "show-environment", "show-messages", "show-options",
    "show-window-options", "source-file", "split-window",
    "start-server", "swap-pane", "swap-window", "switch-client",
    "unbind", "unlink-window", "wait-for",
]


def tmux_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_TMUX_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    if ctx.current.startswith('-'):
        yield from _emit(
            ["-t", "-s", "-n", "-d", "-x", "-y", "-v", "-h", "-p", "-l"],
            ctx.current, "option", "ansired")


# ============================================================
# 12. aws
# ============================================================

_AWS_TOP = [
    "s3", "ec2", "iam", "lambda", "cloudformation", "rds", "sns", "sqs",
    "dynamodb", "ecs", "eks", "kms", "logs", "sts", "route53", "s3api",
    "s3control", "secretsmanager", "ssm", "apigateway", "configure",
    "help",
]


def aws_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_AWS_TOP, ctx.current, "service", "ansiyellow")
        return
    if ctx.current.startswith('--'):
        yield from _emit(
            ["--region", "--profile", "--output", "--query", "--no-cli-pager"],
            ctx.current, "option", "ansired")
        return


# ============================================================
# 13. terraform
# ============================================================

_TF_SUBS = [
    "apply", "console", "destroy", "env", "fmt", "force-unlock", "get",
    "graph", "import", "init", "login", "logout", "output", "plan",
    "providers", "refresh", "show", "state", "taint", "untaint",
    "validate", "version", "workspace",
]


def _tf_workspaces() -> List[str]:
    return cached("tf_ws", 5.0, lambda: [
        line.replace("*", "").strip()
        for line in run_command(["terraform", "workspace", "list"])
    ])


def terraform_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_TF_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    sub = ctx.args[0]
    if sub == "workspace":
        if len(ctx.args) == 1:
            yield from _emit(
                ["list", "select", "new", "delete", "show"],
                ctx.current, "action", "ansiyellow")
        else:
            yield from _emit(_tf_workspaces(), ctx.current, "ws", "ansimagenta")
        return
    if sub in ("state",):
        yield from _emit(
            ["list", "mv", "pull", "push", "rm", "show"],
            ctx.current, "action", "ansiyellow")
        return
    if ctx.current.startswith('-'):
        yield from _emit(
            ["-auto-approve", "-var", "-var-file", "-target",
             "-out", "-input=false", "-lock=true"],
            ctx.current, "option", "ansired")


# ============================================================
# 14. ansible
# ============================================================

_ANSIBLE_SUBS = [
    "playbook", "galaxy", "vault", "doc", "config", "inventory",
    "console", "pull", "lint",
]


def _ansible_playbooks() -> List[str]:
    try:
        return cached("ansible_pb", 5.0, lambda: [
            f for f in os.listdir(os.getcwd())
            if f.endswith(('.yml', '.yaml'))
        ])
    except Exception:
        return []


def ansible_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_ANSIBLE_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    if ctx.args[0] == "playbook":
        if len(ctx.args) == 1:
            yield from _emit(_ansible_playbooks(), ctx.current,
                             "playbook", "ansimagenta")
        else:
            yield from _emit(
                ["-i", "--inventory", "-l", "--limit",
                 "-u", "--user", "--ask-pass", "--ask-become-pass",
                 "-e", "--extra-vars", "-v", "--verbose", "--check"],
                ctx.current, "option", "ansired")


# ============================================================
# 15. helm
# ============================================================

_HELM_SUBS = [
    "create", "dependency", "env", "get", "history", "install", "lint",
    "list", "package", "plugin", "pull", "push", "repo", "rollback",
    "search", "show", "status", "template", "test", "uninstall",
    "upgrade", "verify", "version",
]


def _helm_releases() -> List[str]:
    return cached("helm_releases", 5.0, lambda: [
        line.split()[0] for line in run_command(["helm", "list", "-q"])
    ])


def _helm_repos() -> List[str]:
    return cached("helm_repos", 10.0, lambda: run_command(["helm", "repo", "list", "-o", "name"]))


def helm_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_HELM_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    sub = ctx.args[0]
    if sub in ("uninstall", "upgrade", "rollback", "status", "history",
               "get", "test"):
        yield from _emit(_helm_releases(), ctx.current, "release", "ansimagenta")
        return
    if sub == "repo":
        if len(ctx.args) == 1:
            yield from _emit(
                ["add", "list", "remove", "update", "index"],
                ctx.current, "action", "ansiyellow")
        else:
            yield from _emit(_helm_repos(), ctx.current, "repo", "ansicyan")
        return


# ============================================================
# 16. composer
# ============================================================

_COMPOSER_SUBS = [
    "install", "update", "require", "remove", "create-project",
    "dump-autoload", "dump-env", "config", "diagnose", "doctor",
    "init", "run-script", "show", "self-update", "status", "validate",
    "outdated", "licenses", "archive", "browse", "clear-cache",
    "depends", "prohibits", "why", "why-not", "exec", "global",
]


def _composer_scripts() -> List[str]:
    def _read():
        import json
        try:
            with open(os.path.join(os.getcwd(), "composer.json"),
                      "r", encoding="utf-8") as f:
                data = json.load(f)
            return list((data.get("scripts") or {}).keys())
        except Exception:
            return []
    return cached("composer_scripts", 5.0, _read)


def composer_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_COMPOSER_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    if ctx.args[0] in ("run", "run-script"):
        yield from _emit(_composer_scripts(), ctx.current,
                         "script", "ansimagenta")
        return


# ============================================================
# 17. mvn
# ============================================================

_MVN_PHASES = [
    "validate", "compile", "test", "package", "verify", "install",
    "deploy", "clean", "site", "initialize", "generate-sources",
    "process-sources", "generate-resources", "process-resources",
    "process-classes", "generate-test-sources", "process-test-sources",
    "generate-test-resources", "process-test-resources",
    "test-compile", "process-test-classes", "test", "prepare-package",
    "pre-integration-test", "integration-test", "post-integration-test",
    "pre-site", "post-site", "site-deploy",
]


def mvn_completer(ctx: CompletionContext):
    if ctx.current.startswith('-'):
        yield from _emit(
            ["-D", "-P", "-f", "-o", "-U", "-X", "-e", "-q", "-B", "-s"],
            ctx.current, "option", "ansired")
        return
    yield from _emit(_MVN_PHASES, ctx.current, "phase", "ansimagenta")


# ============================================================
# 18. gradle
# ============================================================

_GRADLE_SUBS = [
    "build", "clean", "test", "assemble", "check", "publish", "run",
    "bootRun", "jar", "war", "install", "dependencies", "projects",
    "tasks", "properties", "init", "wrapper", "help",
]


def gradle_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_GRADLE_SUBS, ctx.current, "task", "ansiyellow")
        return
    if ctx.current.startswith('-'):
        yield from _emit(
            ["-P", "-D", "-q", "-i", "-d", "-s", "--info",
             "--debug", "--stacktrace", "--offline", "--refresh-dependencies"],
            ctx.current, "option", "ansired")


# ============================================================
# 19. dotnet
# ============================================================

_DOTNET_SUBS = [
    "build", "clean", "pack", "publish", "restore", "run", "test",
    "new", "add", "remove", "list", "nuget", "tool", "workload",
    "sln", "vstest", "format", "watch", "help",
]


def dotnet_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_DOTNET_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    sub = ctx.args[0]
    if sub == "new":
        if len(ctx.args) == 1:
            yield from _emit(
                ["console", "classlib", "web", "webapi", "mvc", "blazor",
                 "worker", "xunit", "nunit", "mstest", "sln", "gitignore",
                 "globaljson", "editorconfig"],
                ctx.current, "template", "ansimagenta")
        else:
            yield from _emit(
                ["-n", "--name", "-o", "--output", "-f", "--framework",
                 "--language", "--no-restore"],
                ctx.current, "option", "ansired")
        return
    if sub in ("add", "remove"):
        yield from _emit(
            ["package", "reference", "project"],
            ctx.current, "action", "ansiyellow")


# ============================================================
# 20. conda
# ============================================================

_CONDA_SUBS = [
    "activate", "deactivate", "create", "install", "remove", "update",
    "upgrade", "list", "search", "info", "config", "clean", "env",
    "init", "package", "run", "doctor", "build", "develop",
]


def _conda_envs() -> List[str]:
    return cached("conda_envs", 10.0, lambda: [
        line.split()[-1]
        for line in run_command(["conda", "env", "list"])
        if line.strip() and not line.startswith("#")
    ])


def conda_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_CONDA_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    sub = ctx.args[0]
    if sub in ("activate", "deactivate", "env", "remove"):
        yield from _emit(_conda_envs(), ctx.current, "env", "ansimagenta")
        return
    if ctx.current.startswith('-'):
        yield from _emit(
            ["-n", "--name", "-c", "--channel", "-y", "--yes",
             "-q", "--quiet", "--dry-run"],
            ctx.current, "option", "ansired")


# ============================================================
# 21. poetry
# ============================================================

_POETRY_SUBS = [
    "add", "build", "check", "config", "export", "init", "install",
    "lock", "new", "publish", "remove", "run", "shell", "show",
    "update", "version", "env", "cache", "debug", "source", "about",
]


def _poetry_scripts() -> List[str]:
    def _read():
        try:
            import tomllib
        except ImportError:
            try:
                import tomli as tomllib  # type: ignore
            except ImportError:
                return []
        try:
            with open(os.path.join(os.getcwd(), "pyproject.toml"), "rb") as f:
                data = tomllib.load(f)
            scripts = (data.get("tool") or {}).get("poetry", {}).get("scripts", {})
            return list(scripts.keys())
        except Exception:
            return []
    return cached("poetry_scripts", 5.0, _read)


def poetry_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_POETRY_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    if ctx.args[0] == "run":
        yield from _emit(_poetry_scripts(), ctx.current,
                         "script", "ansimagenta")


# ============================================================
# 22. yarn
# ============================================================

_YARN_SUBS = [
    "add", "audit", "autoclean", "bin", "cache", "check", "config",
    "create", "dedupe", "generate-lock-entry", "global", "help",
    "import", "info", "init", "install", "licenses", "link", "list",
    "login", "logout", "node", "outdated", "owner", "pack", "plugin",
    "policies", "prune", "publish", "remove", "run", "set", "tag",
    "team", "test", "unlink", "unplug", "upgrade", "upgrade-interactive",
    "version", "versions", "why", "workspace", "workspaces",
]


def yarn_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_YARN_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    if ctx.args[0] in ("run", "test"):
        yield from _emit(_npm_scripts(), ctx.current, "script", "ansimagenta")


# ============================================================
# 23. pnpm
# ============================================================

_PNPM_SUBS = [
    "add", "audit", "bin", "config", "create", "dedupe", "deploy",
    "doctor", "env", "fetch", "import", "init", "install", "licenses",
    "link", "list", "list", "outdated", "patch", "patch-commit",
    "prune", "publish", "rebuild", "remove", "root", "run", "server",
    "setup", "start", "store", "test", "unlink", "update", "why",
]


def pnpm_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_PNPM_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    if ctx.args[0] in ("run", "start", "test"):
        yield from _emit(_npm_scripts(), ctx.current, "script", "ansimagenta")


# ============================================================
# 24. pytest
# ============================================================

def _pytest_files() -> List[str]:
    def _read():
        result = []
        try:
            for root, dirs, files in os.walk(os.getcwd()):
                # 限制深度
                if root.count(os.sep) - os.getcwd().count(os.sep) > 3:
                    dirs[:] = []
                    continue
                dirs[:] = [d for d in dirs if not d.startswith(('.', '_'))
                           and d not in ('node_modules', 'venv', '.venv',
                                         '__pycache__')]
                for f in files:
                    if f.startswith("test_") and f.endswith(".py") or \
                            f.endswith("_test.py"):
                        result.append(os.path.relpath(os.path.join(root, f),
                                                      os.getcwd()))
        except Exception:
            pass
        return result
    return cached("pytest_files", 5.0, _read)


def pytest_completer(ctx: CompletionContext):
    if ctx.current.startswith('-'):
        yield from _emit(
            ["-v", "-q", "-s", "-x", "-k", "-m", "--cov",
             "--tb", "--maxfail", "--disable-warnings",
             "-p", "--collect-only"],
            ctx.current, "option", "ansired")
        return
    yield from _emit(_pytest_files(), ctx.current, "test", "ansimagenta")


# ============================================================
# 25. rake
# ============================================================

def _rake_tasks() -> List[str]:
    return cached("rake_tasks", 10.0, lambda: run_command(["rake", "-T"]))


def rake_completer(ctx: CompletionContext):
    if ctx.current.startswith('-'):
        yield from _emit(
            ["-T", "-P", "-D", "-f", "-N", "-v", "-q", "-t"],
            ctx.current, "option", "ansired")
        return
    tasks = [line.split()[1] for line in _rake_tasks()
             if len(line.split()) >= 2]
    yield from _emit(tasks, ctx.current, "task", "ansimagenta")


# ============================================================
# 26. bundle
# ============================================================

_BUNDLE_SUBS = [
    "install", "update", "exec", "add", "remove", "binstubs", "check",
    "clean", "config", "doctor", "env", "gem", "info", "init",
    "inject", "list", "lock", "open", "outdated", "platform",
    "plugin", "pristine", "show", "viz", "cache", "console",
]


def bundle_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_BUNDLE_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    if ctx.args[0] == "exec":
        # 走 PATH
        try:
            from os import environ
            path = environ.get("PATH", "")
            cmds = []
            for d in path.split(os.pathsep):
                if not d or not os.path.isdir(d):
                    continue
                try:
                    for f in os.listdir(d):
                        cmds.append(f)
                except OSError:
                    continue
            yield from _emit(sorted(set(cmds)), ctx.current,
                             "exec", "ansimagenta")
        except Exception:
            pass


# ============================================================
# 27. jupyter
# ============================================================

_JUPYTER_SUBS = [
    "notebook", "lab", "console", "qtconsole", "nbconvert", "nbformat",
    "kernelspec", "kernel", "trust", "run", "execute", "convert",
    "migrate", "troubleshoot", "debug", "contrib", "bundlerextension",
    "serverextension", "nbextension", "nbclassic",
]


def jupyter_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_JUPYTER_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    if ctx.args[0] in ("kernelspec", "kernel"):
        if len(ctx.args) == 1:
            yield from _emit(
                ["list", "install", "uninstall"],
                ctx.current, "action", "ansiyellow")
        return


# ============================================================
# 28. hugo
# ============================================================

_HUGO_SUBS = [
    "new", "build", "server", "config", "convert", "deploy",
    "env", "gen", "import", "list", "mod", "version",
]


def hugo_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_HUGO_SUBS, ctx.current, "subcmd", "ansiyellow")
        return
    if ctx.args[0] == "new":
        if len(ctx.args) == 1:
            yield from _emit(["site", "theme", "content"],
                             ctx.current, "action", "ansiyellow")
        return


# ============================================================
# 29. ffmpeg
# ============================================================

_FFMPEG_COMMON = [
    "-i", "-c", "-c:v", "-c:a", "-b:v", "-b:a", "-r", "-s",
    "-ss", "-t", "-to", "-f", "-vn", "-an", "-y", "-n",
    "-filter_complex", "-vf", "-af", "-crf", "-preset",
    "-pix_fmt", "-map", "-threads", "-loglevel", "-hide_banner",
]

_FFMPEG_FORMATS = [
    "mp4", "mkv", "webm", "avi", "mov", "flv", "wmv", "gif", "mp3",
    "aac", "wav", "flac", "ogg", "opus",
]


def ffmpeg_completer(ctx: CompletionContext):
    if ctx.current.startswith('-'):
        yield from _emit(_FFMPEG_COMMON, ctx.current, "option", "ansired")
        return
    # 文件/目录补全
    try:
        base = os.path.dirname(ctx.current) or "."
        name = os.path.basename(ctx.current)
        if os.path.isdir(base):
            for f in os.listdir(base):
                if f.lower().startswith(name.lower()):
                    full = os.path.join(base, f)
                    is_dir = os.path.isdir(full)
                    yield CompletionItem(
                        text=f + (os.sep if is_dir else ""),
                        meta="dir" if is_dir else "file",
                        style="ansicyan" if is_dir else "ansiwhite",
                        start_position=-len(name) if name else 0,
                    )
    except Exception:
        pass


# ============================================================
# 30. gcloud
# ============================================================

_GCLOUD_TOP = [
    "auth", "compute", "container", "config", "projects", "iam",
    "logging", "storage", "pubsub", "sql", "functions", "app",
    "builds", "deployment-manager", "deploy", "kms", "organizations",
    "services", "source", "spanner", "endpoints", "info", "version",
]


def gcloud_completer(ctx: CompletionContext):
    if not ctx.args:
        yield from _emit(_GCLOUD_TOP, ctx.current, "group", "ansiyellow")
        return
    if ctx.current.startswith('--'):
        yield from _emit(
            ["--project", "--account", "--configuration", "--format",
             "--filter", "--quiet", "--verbosity"],
            ctx.current, "option", "ansired")


# ============================================================
# 注册
# ============================================================

def register(reg):
    reg.register("git",        git_completer,        "Git dynamic completion")
    reg.register("docker",     docker_completer,     "Docker dynamic completion")
    reg.register("kubectl",    kubectl_completer,    "Kubernetes dynamic completion")
    reg.register("npm",        npm_completer,        "npm scripts and subcommands")
    reg.register("pip",        pip_completer,        "pip subcommands and options")
    reg.register("cargo",      cargo_completer,      "cargo subcommands")
    reg.register("go",         go_completer,         "go subcommands and packages")
    reg.register("make",       make_completer,       "Makefile targets")
    reg.register("systemctl",  systemctl_completer,  "systemd units")
    reg.register("ssh",        ssh_completer,        "SSH hosts from ~/.ssh/config")
    reg.register("tmux",       tmux_completer,       "tmux subcommands")
    reg.register("aws",        aws_completer,        "AWS CLI services")
    reg.register("terraform",  terraform_completer,  "terraform subcommands + workspaces")
    reg.register("ansible",    ansible_completer,    "ansible playbooks")
    reg.register("helm",       helm_completer,       "helm releases and repos")
    reg.register("composer",   composer_completer,   "composer scripts")
    reg.register("mvn",        mvn_completer,        "Maven lifecycle phases")
    reg.register("gradle",     gradle_completer,     "Gradle tasks")
    reg.register("dotnet",     dotnet_completer,     "dotnet subcommands")
    reg.register("conda",      conda_completer,      "conda environments")
    reg.register("poetry",     poetry_completer,     "poetry subcommands and scripts")
    reg.register("yarn",       yarn_completer,       "yarn subcommands and scripts")
    reg.register("pnpm",       pnpm_completer,       "pnpm subcommands and scripts")
    reg.register("pytest",     pytest_completer,     "pytest test files")
    reg.register("rake",       rake_completer,       "Rake tasks")
    reg.register("bundle",     bundle_completer,     "bundler subcommands")
    reg.register("jupyter",    jupyter_completer,    "jupyter subcommands")
    reg.register("hugo",       hugo_completer,       "hugo subcommands")
    reg.register("ffmpeg",     ffmpeg_completer,     "ffmpeg options and files")
    reg.register("gcloud",     gcloud_completer,     "gcloud groups and options")