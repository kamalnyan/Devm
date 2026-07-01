#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
python3 scripts/validate-config.py
python3 manager.py --no-llm "frontend login page mobile pe toot raha hai" >/tmp/dev-manager-frontend.txt
python3 manager.py --no-llm "backend docker redis health fail ho raha hai" >/tmp/dev-manager-backend.txt
python3 manager.py --no-llm "architecture review aur release risk audit chahiye" >/tmp/dev-manager-codex.txt
python3 manager.py --doctor >/tmp/dev-manager-doctor.txt || true
python3 manager.py --list-profiles >/tmp/dev-manager-profiles.txt
python3 manager.py --list-roles >/tmp/dev-manager-roles.txt
grep -q "Owner: frontend" /tmp/dev-manager-frontend.txt
grep -q "Owner: backend" /tmp/dev-manager-backend.txt
grep -q "Owner: codex" /tmp/dev-manager-codex.txt
grep -q "Configured profiles" /tmp/dev-manager-profiles.txt
grep -q "Configured role presets" /tmp/dev-manager-roles.txt
echo "dev-manager smoke test passed"
