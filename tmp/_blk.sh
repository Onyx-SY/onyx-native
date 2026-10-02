#!/bin/bash
PORT=8080
log() {
  echo port=$PORT
}
cat <<PY
hello
PY
python3 - <<'PY2' 2>/dev/null
print(1)
PY2
echo after
