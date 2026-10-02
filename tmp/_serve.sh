#!/data/data/com.termux/files/usr/bin/bash
# 五子棋服务器管理脚本
#
# 用法:
#   sh _serve.sh             启动（等同 start）
#   sh _serve.sh start       后台启动 app.py（日志 flask.log，PID 写 smoke.pid）
#   sh _serve.sh stop        停掉当前占用端口的服务
#                            （顺序：smoke.pid → 端口占用者 → 进程命令行匹配；
#                              SIGTERM 后最多等 5 秒，仍活着则 SIGKILL）
#   sh _serve.sh restart     stop + start
#   sh _serve.sh status      查看 PID / 端口占用 / 首页健康检查
#
# 端口: HTTP 5000（app.py 主服务）；5001（可选 WebSocket 推送，装了 websockets 才有）

set -u

PORT=5000
WSPORT=5001
APP=app.py
PIDFILE=smoke.pid
LOG=flask.log

DIR=$(cd "$(dirname "$0")" 2>/dev/null && pwd) || DIR=$(pwd)
cd "$DIR" || exit 1

usage() {
    sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
}

is_alive() {
    [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null
}

# 端口是否已被占用（connect 探测）：0 = 有服务在监听
# 本机实测：Termux(非 root, Android 14) 下 /proc/net/tcp 不可读（Permission denied），
# fuser/lsof/netstat 因此都拿不到端口占用者；端口"是否被占用"只能用 connect 探测。
port_busy() {
    python3 - "$1" <<'PY' 2>/dev/null
import socket
import sys

s = socket.socket()
s.settimeout(1.5)
try:
    s.connect(("127.0.0.1", int(sys.argv[1])))
    sys.exit(0)
except OSError:
    sys.exit(1)
finally:
    s.close()
PY
}

# 方案一（桌面 Linux）：/proc/net/tcp{,6} 里该端口的 socket inode → 扫 /proc/*/fd 反查 PID
proc_net_pids() {
    _port="$1"
    if command -v python3 >/dev/null 2>&1; then
        python3 - "$_port" <<'PY' 2>/dev/null
import os
import sys

port = int(sys.argv[1])
inodes = set()
for path in ("/proc/net/tcp", "/proc/net/tcp6"):
    try:
        with open(path) as f:
            lines = f.read().splitlines()[1:]
    except OSError:
        continue
    for line in lines:
        parts = line.split()
        if len(parts) < 10:
            continue
        try:
            local_port = int(parts[1].split(":")[1], 16)
        except (IndexError, ValueError):
            continue
        if local_port == port:
            inodes.add(parts[9])
if not inodes:
    sys.exit(0)
for pid in os.listdir("/proc"):
    if not pid.isdigit():
        continue
    fd_dir = "/proc/%s/fd" % pid
    try:
        fds = os.listdir(fd_dir)
    except OSError:
        continue
    for fd in fds:
        try:
            link = os.readlink(os.path.join(fd_dir, fd))
        except OSError:
            continue
        if link.startswith("socket:[") and link[8:-1] in inodes:
            print(pid)
            break
PY
    elif command -v fuser >/dev/null 2>&1; then
        fuser -n tcp "$_port" 2>/dev/null | tr -s ' ' '\n' | grep -E '^[0-9]+$'
    elif command -v lsof >/dev/null 2>&1; then
        lsof -ti "tcp:$_port" 2>/dev/null
    fi
}

# 方案二（Termux 主力）：扫 /proc/*/cmdline 找本项目的 app.py 进程
name_pids() {
    python3 - "$APP" <<'PY' 2>/dev/null
import os
import sys

needle = sys.argv[1]
skip = {os.getpid(), os.getppid()}          # 排除扫描进程自身（否则会匹配到 "python3 - app.py"）
for pid in os.listdir("/proc"):
    if not pid.isdigit() or int(pid) in skip:
        continue
    try:
        with open("/proc/%s/cmdline" % pid, "rb") as f:
            cmd = f.read().decode("utf-8", "replace").replace("\x00", " ").strip()
    except OSError:
        continue
    if needle in cmd and "python" in cmd:
        print(pid)
PY
}

# 端口 → 占用进程 PID：先端口反查（桌面 Linux），拿不到再用命令行匹配兜底
port_pids() {
    _pids="$(proc_net_pids "$1")"
    if [ -n "$_pids" ]; then
        echo "$_pids"
    else
        name_pids
    fi
}

# 先 SIGTERM，最多等 5 秒，仍活着再 SIGKILL
kill_wait() {
    _pid="$1"
    kill "$_pid" 2>/dev/null || return 0
    _n=0
    while [ "$_n" -lt 50 ]; do
        is_alive "$_pid" || return 0
        sleep 0.1
        _n=$((_n + 1))
    done
    kill -9 "$_pid" 2>/dev/null
    sleep 0.2
    is_alive "$_pid" && return 1
    return 0
}

do_stop() {
    _found=0

    # 1) PID 文件记录的进程
    if [ -f "$PIDFILE" ]; then
        _pid=$(tr -d ' \n' < "$PIDFILE" 2>/dev/null)
        if is_alive "$_pid"; then
            echo "stop: smoke.pid 记录的 pid $_pid → 结束"
            kill_wait "$_pid"
            _found=1
        fi
        rm -f "$PIDFILE"
    fi

    # 2) 端口占用者（可能是用别的方式启动的实例）
    for _port in "$PORT" "$WSPORT"; do
        port_busy "$_port" || continue
        for _pid in $(port_pids "$_port"); do
            is_alive "$_pid" || continue
            echo "stop: 端口 $_port 被 pid $_pid 占用 → 结束"
            kill_wait "$_pid"
            _found=1
        done
    done

    # 3) 进程命令行兜底（端口信息拿不到时，Termux 下主要靠这条）
    for _pid in $(name_pids); do
        is_alive "$_pid" || continue
        echo "stop: 进程命令行匹配 $APP → 结束 pid $_pid"
        kill_wait "$_pid"
        _found=1
    done

    sleep 0.3
    if port_busy "$PORT"; then
        echo "stop: 失败，端口 $PORT 仍在监听（占用者可能不是本项目进程，或权限不足）"
        return 1
    fi
    if [ "$_found" -eq 0 ]; then
        echo "stop: 端口 $PORT 本来就没有服务在跑"
    else
        echo "stop: 已停止，端口 $PORT 已释放"
    fi
    return 0
}

do_start() {
    if port_busy "$PORT"; then
        echo "start: 端口 $PORT 已被占用（先执行: sh $0 stop）"
        return 1
    fi
    setsid python3 "$APP" >> "$LOG" 2>&1 < /dev/null &
    _pid=$!
    echo "$_pid" > "$PIDFILE"
    sleep 1
    if is_alive "$_pid"; then
        echo "started pid=$_pid  http://127.0.0.1:$PORT"
        return 0
    fi
    echo "start: 启动失败，$LOG 末尾:"
    tail -5 "$LOG" 2>/dev/null
    return 1
}

do_status() {
    if [ -f "$PIDFILE" ] && is_alive "$(tr -d ' \n' < "$PIDFILE")"; then
        echo "pid: $(tr -d ' \n' < "$PIDFILE") (运行中)"
    else
        echo "pid: 无（PID 文件缺失或进程已退出）"
    fi
    for _port in "$PORT" "$WSPORT"; do
        if port_busy "$_port"; then
            _pids=$(port_pids "$_port" | tr '\n' ' ')
            echo "端口 $_port: 占用中 pid=${_pids:-未知}"
        else
            echo "端口 $_port: 空闲"
        fi
    done
    if command -v curl >/dev/null 2>&1; then
        _code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:$PORT/" 2>/dev/null)
        echo "健康检查 GET /: ${_code:-无响应}"
    fi
}

case "${1:-start}" in
    start)   do_start ;;
    stop)    do_stop ;;
    restart) do_stop && do_start ;;
    status)  do_status ;;
    -h|--help|help) usage ;;
    *) echo "未知参数: $1"; echo; usage; exit 2 ;;
esac
