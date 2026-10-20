#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# Absolute path so the web app and the MCP process share one session file
# even when they are started from different working directories.
export TRIALRADAR_DATA="${TRIALRADAR_DATA:-$ROOT/data}"
if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi
export DEMO_CACHE="${DEMO_CACHE:-1}"
export LLM_PROVIDER="${LLM_PROVIDER:-ollama}"
export LLM_FALLBACK="${LLM_FALLBACK:-stub}"
export OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5:7b}"
export HOST="${HOST:-127.0.0.1}"
export PORT="${PORT:-8000}"
PYTHON="${PYTHON:-python3}"
if [[ -x .venv/bin/python ]]; then
  PYTHON=".venv/bin/python"
fi
echo "TrialRadar on http://${HOST}:${PORT}  DEMO_CACHE=${DEMO_CACHE}  LLM_PROVIDER=${LLM_PROVIDER}"
exec "$PYTHON" -m trialradar serve --host "$HOST" --port "$PORT"
