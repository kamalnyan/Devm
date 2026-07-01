from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_RUNS_DIR = PROJECT_ROOT / "runs"
STATE_ROOT = Path.home() / ".dev-manager"
STATE_PATH = STATE_ROOT / "state.json"
STATE_RUNS_DIR = STATE_ROOT / "runs"


def load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "projects": {}, "runs": []}


def save_run_record(task: str, repo: str, evidence: dict, compact: dict, gui_result: dict | None) -> str:
    LOCAL_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    STATE_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    STATE_ROOT.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = _slug(task)
    payload = _payload(task, repo, evidence, compact, gui_result)

    local_path = LOCAL_RUNS_DIR / f"{stamp}-{slug}.json"
    state_path = STATE_RUNS_DIR / f"{stamp}-{slug}.json"
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    local_path.write_text(text, encoding="utf-8")
    state_path.write_text(text, encoding="utf-8")

    state = load_state()
    projects = state.setdefault("projects", {})
    project = projects.setdefault(repo, {"first_seen": payload["created_at"], "runs": 0})
    project["last_seen"] = payload["created_at"]
    project["runs"] = int(project.get("runs") or 0) + 1
    project["last_owner"] = payload["route"]["owner"]
    project["last_target_app"] = payload["route"]["target_app"]
    project["last_task"] = task
    state.setdefault("runs", []).append({"created_at": payload["created_at"], "repo": repo, "task": task, "path": str(state_path)})
    state["runs"] = state["runs"][-100:]
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(local_path)


def _payload(task: str, repo: str, evidence: dict, compact: dict, gui_result: dict | None) -> dict:
    return {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "repo": repo,
        "task": task,
        "route": {
            "owner": compact.get("owner"),
            "target_app": compact.get("target_app"),
            "confidence": compact.get("confidence"),
            "reason": compact.get("reason"),
            "profile": compact.get("profile"),
            "role": compact.get("role"),
        },
        "safe_commands": compact.get("suggested_safe_commands", []),
        "skills": compact.get("skills", {}),
        "search_hits": compact.get("search_hits", []),
        "handoff_prompt": evidence.get("handoff", {}).get("prompt"),
        "gui_result": gui_result,
    }


def _slug(text: str) -> str:
    keep = []
    for char in text.lower():
        if char.isalnum():
            keep.append(char)
        elif keep and keep[-1] != "-":
            keep.append("-")
    slug = "".join(keep).strip("-")[:60]
    return slug or "task"
