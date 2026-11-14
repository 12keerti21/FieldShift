#!/usr/bin/env bash
# Start FieldShift. Bundles must exist first - simulating a site takes about a
# minute, so it is done ahead of time rather than inside a web request.
set -euo pipefail
cd "$(dirname "$0")/.."
if ! ls data/bundles/*.json >/dev/null 2>&1; then
  echo "No precomputed sites. Building them now (a few minutes)..."
  PYTHONPATH=src ./.venv/bin/python -m fieldshift.api.bundle
fi
echo "FieldShift on http://127.0.0.1:8000"
exec PYTHONPATH=src ./.venv/bin/uvicorn fieldshift.api.main:app \
  --host 127.0.0.1 --port 8000 "$@"
