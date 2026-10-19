#!/usr/bin/env bash
# One-command setup for a laptop demo. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== Python environment =="
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

echo "== Tesseract (optional photo path) =="
if command -v tesseract >/dev/null 2>&1; then
  tesseract --version | head -n 1
else
  case "$(uname -s)" in
    Darwin)
      if command -v brew >/dev/null 2>&1; then brew install tesseract || true; fi
      ;;
    Linux)
      if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update && sudo apt-get install -y tesseract-ocr || true
      elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y tesseract || true
      fi
      ;;
  esac
  if ! command -v tesseract >/dev/null 2>&1; then
    echo "Tesseract is not installed. Typed notes and text PDFs still work."
    echo "  macOS:  brew install tesseract"
    echo "  Debian: sudo apt-get install tesseract-ocr"
    echo "  Fedora: sudo dnf install tesseract"
  fi
fi

echo "== Ollama model (optional) =="
if command -v ollama >/dev/null 2>&1; then
  ollama pull "${OLLAMA_MODEL:-qwen2.5:7b}" || echo "ollama pull failed; the demo cache and local rules engine still work."
else
  echo "Ollama is not installed. Install it from https://ollama.com then:"
  echo "  ollama pull qwen2.5:7b"
  echo "The app will use the local rules engine until Ollama is running."
  echo "Set DEMO_CACHE=1 (the default in scripts/run.sh) to demo without a model."
fi

echo
echo "Ready. Start the app with: ./scripts/run.sh"
