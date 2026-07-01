#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -z "${PYTHON_BIN:-}" ]; then
  if command -v python3.12 >/dev/null 2>&1; then
    PYTHON_BIN="python3.12"
  else
    PYTHON_BIN="python3"
  fi
fi

"$PYTHON_BIN" -m venv .venv-adk
.venv-adk/bin/pip install --upgrade pip
.venv-adk/bin/pip install google-adk litellm

if command -v ollama >/dev/null 2>&1; then
  ollama pull "${DEV_MANAGER_MODEL:-glm4:latest}" || true
else
  echo "Ollama is not installed. Install Ollama and run: ollama pull glm4"
fi

./scripts/install-devm.sh
python3 scripts/validate-config.py
echo "Bootstrap complete. Try: devm --list-agents"
