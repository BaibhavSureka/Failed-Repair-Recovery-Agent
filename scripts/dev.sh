#!/usr/bin/env bash
set -euo pipefail
TASK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$TASK_ROOT"
if [[ -x .tools/node/bin/node ]]; then
  export PATH="$TASK_ROOT/.tools/node/bin:$PATH"
elif [[ -x .tools/node/node_modules/.bin/node ]]; then
  export PATH="$TASK_ROOT/.tools/node/node_modules/.bin:$PATH"
fi
node -e 'const [major, minor] = process.versions.node.split(".").map(Number); if (major < 20 || (major === 20 && minor < 9)) { console.error("Node.js 20.9+ is required. See README.md for a local Node 22 setup."); process.exit(1); }'
if [[ ! -x .venv/bin/python || ! -d frontend/node_modules ]]; then
  echo 'Install dependencies first; see README.md.' >&2
  exit 1
fi
if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi
TASK_API_PID=''
TASK_UI_PID=''
cleanup() {
  [[ -z "$TASK_API_PID" ]] || kill "$TASK_API_PID" 2>/dev/null || true
  [[ -z "$TASK_UI_PID" ]] || kill "$TASK_UI_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM
(
  cd backend
  exec "$TASK_ROOT/.venv/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
) &
TASK_API_PID=$!
(
  cd frontend
  exec node node_modules/next/dist/bin/next dev --hostname 127.0.0.1 --port 3000
) &
TASK_UI_PID=$!
echo 'Dashboard: http://localhost:3000 | API docs: http://localhost:8000/docs'
# Bash 3.2 on macOS does not provide `wait -n`.
while kill -0 "$TASK_API_PID" 2>/dev/null && kill -0 "$TASK_UI_PID" 2>/dev/null; do
  sleep 1
done
if kill -0 "$TASK_API_PID" 2>/dev/null; then
  wait "$TASK_UI_PID"
else
  wait "$TASK_API_PID"
fi
