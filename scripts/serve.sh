#!/usr/bin/env bash
# Start FieldShift.
#
# Bundles must exist first: simulating one site takes about a minute, so it is done
# ahead of time rather than inside a web request.
#
#   ./scripts/serve.sh            # port 8000
#   ./scripts/serve.sh --port 8080
set -euo pipefail
cd "$(dirname "$0")/.."

PORT=8000
ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    *) ARGS+=("$1"); shift ;;
  esac
done

if [[ ! -x .venv/bin/uvicorn ]]; then
  echo "error: .venv is missing or incomplete. Create it with:" >&2
  echo "  python3.11 -m venv .venv && ./.venv/bin/python -m pip install -r requirements.txt" >&2
  exit 1
fi

if ! ls data/bundles/*.json >/dev/null 2>&1; then
  echo "No precomputed sites yet. Building them (a few minutes)..."
  PYTHONPATH=src ./.venv/bin/python -m fieldshift.api.bundle
fi

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "error: port $PORT is already in use. Try: ./scripts/serve.sh --port 8001" >&2
  exit 1
fi

echo
echo "  FieldShift  -  Rethink What Grows Next."
echo "  open  http://127.0.0.1:${PORT}"
echo "  stop  Ctrl-C"
echo

# The assignment must precede `exec`; `exec VAR=x cmd` tries to run a program
# literally named "VAR=x" and fails with "not found".
export PYTHONPATH=src
# macOS ships bash 3.2, where expanding an empty array under `set -u` is an
# "unbound variable" error. The ${ARGS[@]+...} form expands to nothing when the
# array is empty instead of failing.
exec ./.venv/bin/uvicorn fieldshift.api.main:app \
  --host 127.0.0.1 --port "$PORT" ${ARGS[@]+"${ARGS[@]}"}
