"""Background job management for autonomous solve runs.

Jobs are stored as JSON files in ~/.devmanager/jobs/.
Each job has: id, status (pending/running/done/failed), task, result, timestamps.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


JOBS_DIR = Path.home() / ".devmanager" / "jobs"


# ─────────────────────────────────────────────────────────────────────────────
# Job file I/O
# ─────────────────────────────────────────────────────────────────────────────

def _jobs_dir() -> Path:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    return JOBS_DIR


def _job_path(job_id: str) -> Path:
    return _jobs_dir() / f"{job_id}.json"


def create_job(task: str, repo: str, extra: dict | None = None) -> str:
    """Create a new job record, return job_id."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = _slug(task)[:30]
    job_id = f"{stamp}-{slug}"
    record = {
        "id": job_id,
        "status": "pending",
        "task": task,
        "repo": repo,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "started_at": None,
        "finished_at": None,
        "pid": None,
        "result": None,
        "error": None,
        **(extra or {}),
    }
    _job_path(job_id).write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return job_id


def update_job(job_id: str, **fields: Any) -> None:
    path = _job_path(job_id)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        record = {"id": job_id}
    record.update(fields)
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")


def load_job(job_id: str) -> dict | None:
    path = _job_path(job_id)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def list_jobs(limit: int = 20) -> list[dict]:
    """Return jobs sorted newest first."""
    jobs = []
    for p in sorted(_jobs_dir().glob("*.json"), reverse=True)[:limit]:
        try:
            jobs.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    return jobs


def delete_job(job_id: str) -> bool:
    path = _job_path(job_id)
    if path.exists():
        path.unlink()
        return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Spawn background job
# ─────────────────────────────────────────────────────────────────────────────

def spawn_background(
    task: str,
    repo: str,
    extra_args: list[str] | None = None,
) -> str:
    """Spawn a detached background solver process. Returns job_id."""
    job_id = create_job(task, repo)

    # Python executable (same venv as the caller)
    python = sys.executable

    # Build the command: python -m devmanager._bg_worker <job_id> <repo> <task> [extra_args...]
    cmd = [python, "-m", "devmanager._bg_worker", job_id, repo, task] + (extra_args or [])

    # Detach: no stdout/stderr (result written to job file)
    log_path = _jobs_dir() / f"{job_id}.log"
    log_file = open(log_path, "w")  # noqa: WPS515

    kwargs: dict[str, Any] = {
        "stdout": log_file,
        "stderr": log_file,
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform != "win32":
        kwargs["start_new_session"] = True  # detach from terminal

    proc = subprocess.Popen(cmd, **kwargs)
    update_job(job_id, status="running", pid=proc.pid,
               started_at=datetime.now().isoformat(timespec="seconds"))
    return job_id


# ─────────────────────────────────────────────────────────────────────────────
# Print helpers
# ─────────────────────────────────────────────────────────────────────────────

def print_jobs(limit: int = 20) -> None:
    BOLD = "\033[1m"; DIM = "\033[2m"; GREEN = "\033[32m"
    YELLOW = "\033[33m"; RED = "\033[31m"; CYAN = "\033[36m"; RESET = "\033[0m"

    jobs = list_jobs(limit)
    if not jobs:
        print("No background jobs yet. Run: devm --bg \"your task\"")
        return

    print(f"\n{BOLD}Background Jobs:{RESET}\n")
    for job in jobs:
        status = job.get("status", "?")
        if status == "done":
            badge = f"{GREEN}✓ done   {RESET}"
        elif status == "running":
            badge = f"{YELLOW}⟳ running{RESET}"
        elif status == "failed":
            badge = f"{RED}✗ failed {RESET}"
        else:
            badge = f"{DIM}· pending{RESET}"

        jid = job["id"]
        task = job.get("task", "?")[:60]
        created = (job.get("created_at") or "")[:16].replace("T", " ")
        print(f"  {badge}  {BOLD}{jid}{RESET}")
        print(f"           {DIM}{created}{RESET}  {task}")
        if status == "running" and job.get("pid"):
            print(f"           {DIM}PID {job['pid']}{RESET}")
        if status == "done":
            result_preview = (job.get("result") or "")[:100].replace("\n", " ")
            if result_preview:
                print(f"           {CYAN}{result_preview}…{RESET}")
        if status == "failed" and job.get("error"):
            print(f"           {RED}{job['error'][:80]}{RESET}")
        print()

    print(f"  {DIM}devm result <job-id>   devm jobs clear{RESET}\n")


def print_result(job_id: str) -> int:
    BOLD = "\033[1m"; DIM = "\033[2m"; GREEN = "\033[32m"
    RED = "\033[31m"; YELLOW = "\033[33m"; RESET = "\033[0m"

    # Allow partial match (first few chars of job_id)
    if not _job_path(job_id).exists():
        # Try prefix match
        matches = [p for p in _jobs_dir().glob("*.json") if p.stem.startswith(job_id)]
        if len(matches) == 1:
            job_id = matches[0].stem
        elif len(matches) > 1:
            print(f"Ambiguous job ID prefix '{job_id}'. Matches:")
            for m in matches:
                print(f"  {m.stem}")
            return 1
        else:
            print(f"Job not found: {job_id}")
            print("Run 'devm jobs' to list jobs.")
            return 1

    job = load_job(job_id)
    if not job:
        print(f"Job not found: {job_id}")
        return 1

    status = job.get("status", "?")
    print(f"\n{BOLD}Job: {job_id}{RESET}")
    print(f"Task: {job.get('task', '?')}")
    print(f"Repo: {job.get('repo', '?')}")
    print(f"Status: {status}  Created: {(job.get('created_at') or '')[:16]}")

    if status == "running":
        pid = job.get("pid")
        print(f"\n{YELLOW}⟳ Still running (PID {pid}){RESET}")
        log = _jobs_dir() / f"{job_id}.log"
        if log.exists():
            tail = log.read_text(encoding="utf-8", errors="replace")[-2000:]
            if tail.strip():
                print(f"\n{DIM}-- Live log (tail) --{RESET}")
                print(tail)
        return 0

    if status == "failed":
        print(f"\n{RED}✗ Failed:{RESET}")
        print(job.get("error") or "(no error message)")
        return 1

    if status == "done":
        print(f"\n{GREEN}✓ Result:{RESET}")
        print(f"{DIM}{'─' * 60}{RESET}")
        print(job.get("result") or "(empty result)")
        print(f"{DIM}{'─' * 60}{RESET}")
        finished = (job.get("finished_at") or "")[:16]
        if finished:
            print(f"\n{DIM}Finished at {finished}{RESET}")
        return 0

    print(f"\n{DIM}Status: {status} — job may still be starting{RESET}")
    return 0


def _slug(text: str) -> str:
    keep = []
    for char in text.lower():
        if char.isalnum():
            keep.append(char)
        elif keep and keep[-1] != "-":
            keep.append("-")
    return "".join(keep).strip("-") or "job"
