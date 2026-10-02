source tmp/_t1.sh
[[ -n "$T1_VAR" ]] && T2_COND=yes || T2_COND=no
T2_ESC=$'line1\nline2'
case "$T1_VAR" in hello*) T2_CASE=match;; *) T2_CASE=no;; esac
T2_SUM=0; for i in 1 2 3; do T2_SUM=$((T2_SUM + i)); done
