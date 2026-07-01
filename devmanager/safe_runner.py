"""Safe command runner — allowlist loaded from config/safe-commands.json."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "safe-commands.json"
_FALLBACK_ALLOWED = [
    "npm run build",
    "npm run test",
    "npm run lint",
    "docker compose up -d --build",
    "docker compose down",
    "flutter analyze",
    "flutter test",
]
_BLOCKED_PATTERNS = [
    "deploy",
    "push",
    "prisma migrate",
    "docker compose down -v",
    "rm -rf",
    "production",
]


def _load_config() -> tuple[list[str], list[str]]:
    try:
        data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        return data.get("allowed", _FALLBACK_ALLOWED), data.get("blocked_by_default", _BLOCKED_PATTERNS)
    except (OSError, json.JSONDecodeError):
        return _FALLBACK_ALLOWED, _BLOCKED_PATTERNS


def run_safe_commands(commands: list[str], cwd: Path) -> list[dict]:
    allowed, blocked = _load_config()
    results = []
    for command in commands:
        if not _is_allowed(command, allowed, blocked):
            results.append({"command": command, "returncode": 126, "summary": "Blocked by safe runner."})
            continue
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                shell=True,
                text=True,
                capture_output=True,
                timeout=180,
                check=False,
            )
        except subprocess.TimeoutExpired:
            results.append({"command": command, "returncode": 124, "summary": "Timed out after 180s."})
            continue
        output = (completed.stdout + "\n" + completed.stderr).strip()
        results.append({"command": command, "returncode": completed.returncode, "summary": output[-1000:]})
    return results


def _is_allowed(command: str, allowed: list[str], blocked: list[str]) -> bool:
    cmd_lower = command.lower()
    for pattern in blocked:
        if pattern.lower() in cmd_lower:
            return False
    return any(command == entry or command.endswith(entry) for entry in allowed)
