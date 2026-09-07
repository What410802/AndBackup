#!/bin/sh
# POSIX wrapper; the complete implementation is shared with Windows in
# backup.py. Use exec so the child's exit code and signals are preserved.
set -eu
case "$0" in
    */*) HERE=${0%/*} ;;
    *) HERE=. ;;
esac
HERE=$(CDPATH= cd -- "$HERE" && pwd)
if [ -n "${PYTHON:-}" ]; then
    PY=$PYTHON
else
    PY=$(command -v python3 || command -v python || true)
fi
[ -n "$PY" ] || { echo '[ERROR] python3/python (Python 3) was not found' >&2; exit 1; }
exec "$PY" "$HERE/backup.py" "$@"
