from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from devmanager.context import collect_context
from devmanager.gui_bridge import send_to_gui_app as paste_gui
from devmanager.handoff import build_handoff
from devmanager.router import classify_task
from devmanager.safe_runner import run_safe_commands


MAX_OUTPUT = 6000


def inspect_repo(repo_root: str) -> dict:
    """Read repo guidance files and summarize known project folders."""
    root = _safe_root(repo_root)
    return collect_context(root)


def route_task(repo_root: str, task: str, profile: str = "", role: str = "") -> dict:
    """Classify the task and build a paste-ready handoff prompt."""
    root = _safe_root(repo_root)
    context = collect_context(root)
    route = classify_task(task, context)
    return build_handoff(task=task, route=route, context=context, used_llm=False, profile=profile or None, role=role or None)


def search_code(repo_root: str, query: str, scope: str = "") -> dict:
    """Search repo code with ripgrep using bounded output."""
    root = _safe_root(repo_root)
    search_root = _safe_child(root, scope) if scope else root
    rg = _find_rg()
    if rg:
        command = [
            rg,
            "--line-number",
            "--hidden",
            "--glob",
            "!node_modules",
            "--glob",
            "!dist",
            "--glob",
            "!.git",
            "--glob",
            "!.next",
            "--glob",
            "!build",
            query,
            str(search_root),
        ]
    else:
        command = ["grep", "-RIn", "--exclude-dir=.git", "--exclude-dir=node_modules", "--exclude-dir=dist", "--exclude-dir=.next", query, str(search_root)]
    completed = subprocess.run(command, text=True, capture_output=True, timeout=30, check=False)
    output = (completed.stdout + completed.stderr).strip()
    return {
        "command": " ".join(command),
        "returncode": completed.returncode,
        "output": output[:MAX_OUTPUT],
        "truncated": len(output) > MAX_OUTPUT,
    }


def _find_rg() -> str | None:
    for candidate in (
        shutil.which("rg"),
        "/opt/homebrew/bin/rg",
        "/usr/local/bin/rg",
        "/Applications/Codex.app/Contents/Resources/rg",
    ):
        if candidate and Path(candidate).exists():
            return candidate
    return None


def run_safe_command(repo_root: str, command: str) -> dict:
    """Run one allowlisted local verification command."""
    root = _safe_root(repo_root)
    results = run_safe_commands([command], cwd=root)
    return results[0] if results else {"command": command, "returncode": 126, "summary": "No result."}


def send_to_gui_app(app_name: str, prompt: str, submit: bool = False) -> dict:
    """Open an installed GUI agent app and paste a prompt into it."""
    return paste_gui(app_name=app_name, prompt=prompt, submit=submit)


def _safe_root(repo_root: str) -> Path:
    root = Path(repo_root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Repo root not found: {root}")
    return root


def _safe_child(root: Path, scope: str) -> Path:
    clean = scope.strip().lstrip("/")
    candidate = (root / clean).resolve()
    common = os.path.commonpath([str(root), str(candidate)])
    if common != str(root):
        raise ValueError("Scope must stay inside repo root.")
    if not candidate.exists():
        raise ValueError(f"Scope not found: {candidate}")
    return candidate
