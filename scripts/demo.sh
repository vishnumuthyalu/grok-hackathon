#!/usr/bin/env bash
# Side-by-side demo: web app in the browser, stepped bot in this terminal.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export TRIALRADAR_DATA="${TRIALRADAR_DATA:-$ROOT/data}"
export DEMO_CACHE="${DEMO_CACHE:-1}"
export LLM_PROVIDER="${LLM_PROVIDER:-ollama}"
export LLM_FALLBACK="${LLM_FALLBACK:-stub}"
export HOST="${HOST:-127.0.0.1}"
export PORT="${PORT:-8000}"
URL="http://${HOST}:${PORT}"
mkdir -p "$TRIALRADAR_DATA"

PYTHON="${PYTHON:-python3}"
if [[ -x .venv/bin/python ]]; then
  PYTHON=".venv/bin/python"
fi

if ! curl -sf "$URL/api/health" >/dev/null 2>&1; then
  echo "Starting TrialRadar at $URL"
  nohup ./scripts/run.sh >"$TRIALRADAR_DATA/server.log" 2>&1 &
  echo $! >"$TRIALRADAR_DATA/server.pid"
  for _ in $(seq 1 80); do
    if curl -sf "$URL/api/health" >/dev/null 2>&1; then
      break
    fi
    sleep 0.25
  done
else
  echo "TrialRadar is already running at $URL"
fi

if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$URL" >/dev/null 2>&1 || true
elif command -v open >/dev/null 2>&1; then
  open "$URL" >/dev/null 2>&1 || true
else
  echo "Open $URL"
fi

echo "Browser: $URL    Bot: this terminal. Press Enter before each step."
"$PYTHON" "$ROOT/scripts/mcp_client_demo.py" "$@"
