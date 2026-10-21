#!/usr/bin/env bash
set -euo pipefail
TASK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$TASK_ROOT"
if [[ -x .tools/node/bin/node ]]; then
  export PATH="$TASK_ROOT/.tools/node/bin:$PATH"
elif [[ -x .tools/node/node_modules/.bin/node ]]; then
  export PATH="$TASK_ROOT/.tools/node/node_modules/.bin:$PATH"
fi
.venv/bin/python -m pytest backend/tests -q
cd frontend
npm run typecheck
npm run build
