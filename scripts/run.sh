#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
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
