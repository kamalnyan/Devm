#!/usr/bin/env bash
set -euo pipefail

MODEL="${DEV_MANAGER_MODEL:-glm4:latest}"

if ! command -v ollama >/dev/null 2>&1; then
  echo "Ollama is not installed."
  echo "Install with: brew install ollama"
  exit 1
fi

if ! curl -fsS http://127.0.0.1:11434/api/tags >/tmp/dev-manager-ollama-tags.json 2>/dev/null; then
  echo "Ollama is installed but not running."
  echo "Start it with: ollama serve"
  exit 1
fi

if ! grep -q "\"name\":\"${MODEL}\"" /tmp/dev-manager-ollama-tags.json; then
  echo "GLM model not found locally: ${MODEL}"
  echo "Pull it with: ollama pull ${MODEL%%:*}"
  exit 1
fi

echo "GLM ready: ${MODEL}"
