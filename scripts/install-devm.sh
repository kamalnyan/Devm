#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="${1:-/opt/homebrew/bin}"
TARGET="$BIN_DIR/devm"

mkdir -p "$BIN_DIR"

cat > "$TARGET" <<EOF
#!/bin/zsh
set -e

MANAGER_DIR="$PROJECT_DIR"
CALLER_DIR="\$PWD"
HAS_REPO=0
HAS_GUI=0
HAS_SUBMIT_FLAG=0

for arg in "\$@"; do
  if [ "\$arg" = "--repo" ]; then
    HAS_REPO=1
  fi
  if [ "\$arg" = "--gui" ] || [ "\$arg" = "--auto-gui" ]; then
    HAS_GUI=1
  fi
  if [ "\$arg" = "--submit" ] || [ "\$arg" = "--no-submit" ]; then
    HAS_SUBMIT_FLAG=1
  fi
done

cd "\$MANAGER_DIR"

SUBMIT_ARGS=()
if [ "\$HAS_SUBMIT_FLAG" -eq 0 ]; then
  SUBMIT_ARGS=(--submit)
fi

if [ "\$HAS_REPO" -eq 1 ]; then
  if [ "\$HAS_GUI" -eq 1 ]; then
    exec "\$MANAGER_DIR/.venv-adk/bin/python" "\$MANAGER_DIR/adk_manager.py" "\${SUBMIT_ARGS[@]}" "\$@"
  else
    exec "\$MANAGER_DIR/.venv-adk/bin/python" "\$MANAGER_DIR/adk_manager.py" --auto-gui "\${SUBMIT_ARGS[@]}" "\$@"
  fi
else
  if [ "\$HAS_GUI" -eq 1 ]; then
    exec "\$MANAGER_DIR/.venv-adk/bin/python" "\$MANAGER_DIR/adk_manager.py" --repo "\$CALLER_DIR" "\${SUBMIT_ARGS[@]}" "\$@"
  else
    exec "\$MANAGER_DIR/.venv-adk/bin/python" "\$MANAGER_DIR/adk_manager.py" --repo "\$CALLER_DIR" --auto-gui "\${SUBMIT_ARGS[@]}" "\$@"
  fi
fi
EOF

chmod +x "$TARGET"
echo "Installed devm -> $TARGET"
