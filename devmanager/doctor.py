from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from devmanager.agent_config import DEFAULT_CONFIG_PATH, DEFAULT_PROFILES_PATH, allowed_gui_apps, load_agent_config, load_profiles_config
from devmanager.state import STATE_PATH, STATE_ROOT


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def build_doctor_report(repo: str | None = None, model: str = "glm4:latest") -> dict:
    checks = [
        _check_file("agents_config", DEFAULT_CONFIG_PATH),
        _check_file("profiles_config", DEFAULT_PROFILES_PATH),
        _check_agents_config(),
        _check_profiles_config(),
        _check_skills_dir(),
        _check_agents_dir(),
        _check_python_env(),
        _check_ollama(model),
        _check_devm_wrapper(),
        _check_gui_apps(),
        _check_state(),
    ]
    if repo:
        checks.append(_check_repo(Path(repo).expanduser().resolve()))
    status = "ok"
    if any(check["status"] == "error" for check in checks):
        status = "error"
    elif any(check["status"] == "warning" for check in checks):
        status = "warning"
    return {"status": status, "checks": checks}


def print_doctor_report(report: dict, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return
    print("Dev Manager Doctor")
    print(f"Status: {report['status'].upper()}")
    for check in report["checks"]:
        print(f"- {check['status'].upper()}: {check['name']} - {check['message']}")
        if check.get("hint"):
            print(f"  hint: {check['hint']}")


def _check_file(name: str, path: Path) -> dict:
    if path.exists():
        return {"name": name, "status": "ok", "message": str(path)}
    return {"name": name, "status": "error", "message": f"Missing {path}"}


def _check_agents_config() -> dict:
    config = load_agent_config()
    ids = [agent["id"] for agent in config.get("agents", [])]
    if not ids:
        return {"name": "agents", "status": "error", "message": "No agents configured."}
    if config.get("default_owner") not in ids:
        return {"name": "agents", "status": "error", "message": "default_owner does not match an agent id."}
    return {"name": "agents", "status": "ok", "message": f"{len(ids)} configured: {', '.join(ids)}"}


def _check_profiles_config() -> dict:
    profiles = load_profiles_config()
    names = sorted(profiles.get("profiles", {}).keys())
    if not names:
        return {"name": "profiles", "status": "error", "message": "No profiles configured."}
    return {"name": "profiles", "status": "ok", "message": f"default={profiles['default']}; profiles={', '.join(names)}"}


def _check_skills_dir() -> dict:
    skills_dir = PROJECT_ROOT / "skills"
    if not skills_dir.exists():
        return {"name": "skills_dir", "status": "warning", "message": "skills/ not found",
                "hint": "Run 'devm repair' to restore defaults."}
    count = sum(1 for p in skills_dir.iterdir() if (p / "SKILL.md").exists())
    return {"name": "skills_dir", "status": "ok", "message": f"{count} skill(s) in skills/"}


def _check_agents_dir() -> dict:
    agents_dir = PROJECT_ROOT / "agents"
    if not agents_dir.exists():
        return {"name": "agents_dir", "status": "warning", "message": "agents/ not found",
                "hint": "Run 'devm repair' to restore defaults."}
    count = len(list(agents_dir.glob("*.md")))
    return {"name": "agents_dir", "status": "ok", "message": f"{count} agent definition(s) in agents/"}


def _check_python_env() -> dict:
    for venv in (".venv", ".venv-adk"):
        python = PROJECT_ROOT / venv / "bin" / "python"
        if python.exists():
            return {"name": "python_env", "status": "ok", "message": str(python)}
    return {"name": "python_env", "status": "error", "message": "No .venv found", "hint": "./install.sh"}


def _check_ollama(model: str) -> dict:
    if not shutil.which("ollama"):
        return {"name": "ollama", "status": "warning", "message": "ollama command not found", "hint": "Install Ollama, then run: ollama pull glm4"}
    completed = subprocess.run(["ollama", "list"], text=True, capture_output=True, timeout=10, check=False)
    if completed.returncode != 0:
        return {"name": "ollama", "status": "warning", "message": "ollama list failed", "hint": "Start Ollama and retry."}
    if model.split(":", 1)[0] in completed.stdout or model in completed.stdout:
        return {"name": "ollama", "status": "ok", "message": f"model available: {model}"}
    return {"name": "ollama", "status": "warning", "message": f"model not listed: {model}", "hint": f"ollama pull {model.split(':', 1)[0]}"}


def _check_devm_wrapper() -> dict:
    path = shutil.which("devm")
    if path:
        return {"name": "devm_wrapper", "status": "ok", "message": path}
    return {"name": "devm_wrapper", "status": "warning", "message": "devm not found in PATH", "hint": "./scripts/install-devm.sh"}


def _check_gui_apps() -> dict:
    missing = []
    for app in sorted(set(allowed_gui_apps().values())):
        if not (Path("/Applications") / f"{app}.app").exists():
            missing.append(app)
    if not missing:
        return {"name": "gui_apps", "status": "ok", "message": "All configured GUI apps found."}
    return {"name": "gui_apps", "status": "warning", "message": "Missing apps: " + ", ".join(missing), "hint": "Edit config/agents.json or install the missing app."}


def _check_state() -> dict:
    if STATE_PATH.exists():
        return {"name": "state", "status": "ok", "message": str(STATE_PATH)}
    return {"name": "state", "status": "warning", "message": f"No state yet under {STATE_ROOT}", "hint": "Run devm once to create state."}


def _check_repo(repo: Path) -> dict:
    if not repo.exists():
        return {"name": "repo", "status": "error", "message": f"Repo not found: {repo}"}
    guidance = [name for name in ("AGENTS.md", "HARNESS.md", "ACCESS-MODEL.md") if (repo / name).exists()]
    message = f"{repo}; guidance={', '.join(guidance) if guidance else 'none'}"
    return {"name": "repo", "status": "ok", "message": message}
