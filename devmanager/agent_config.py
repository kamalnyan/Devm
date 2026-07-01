from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "agents.json"
DEFAULT_PROFILES_PATH = PROJECT_ROOT / "config" / "profiles.json"


@lru_cache(maxsize=8)
def load_agent_config(path: str | None = None) -> dict[str, Any]:
    config_path = Path(path).expanduser().resolve() if path else DEFAULT_CONFIG_PATH
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = _fallback_config()
    return _normalize_config(data)


def agents_by_id(config: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    cfg = config or load_agent_config()
    return {agent["id"]: agent for agent in cfg["agents"]}


def agent_for_owner(owner: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or load_agent_config()
    by_id = agents_by_id(cfg)
    return by_id.get(owner) or by_id[cfg["default_owner"]]


def allowed_gui_apps(config: dict[str, Any] | None = None) -> dict[str, str]:
    cfg = config or load_agent_config()
    apps = {}
    for agent in cfg["agents"]:
        app = agent.get("app") or agent.get("name")
        if app:
            apps[app.lower()] = app
    return apps


@lru_cache(maxsize=8)
def load_profiles_config(path: str | None = None) -> dict[str, Any]:
    config_path = Path(path).expanduser().resolve() if path else DEFAULT_PROFILES_PATH
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = _fallback_profiles()
    return _normalize_profiles(data)


def profile_by_name(name: str | None = None) -> dict[str, Any]:
    profiles = load_profiles_config()
    selected = name or profiles["default"]
    return profiles["profiles"].get(selected) or profiles["profiles"][profiles["default"]]


def role_preset(role: str | None, config: dict[str, Any] | None = None) -> dict[str, str] | None:
    if not role:
        return None
    cfg = config or load_agent_config()
    preset = (cfg.get("role_presets") or {}).get(role)
    if not preset:
        return None
    return {
        "id": role,
        "label": str(preset.get("label") or role),
        "prefix": str(preset.get("prefix") or ""),
    }


def list_roles(config: dict[str, Any] | None = None) -> list[dict[str, str]]:
    cfg = config or load_agent_config()
    result = []
    for role, preset in sorted((cfg.get("role_presets") or {}).items()):
        result.append({"id": role, "label": str(preset.get("label") or role), "prefix": str(preset.get("prefix") or "")})
    return result


def matched_skills(task: str, profile_name: str | None = None, config: dict[str, Any] | None = None) -> dict[str, list[dict[str, str]]]:
    cfg = config or load_agent_config()
    profile = profile_by_name(profile_name)
    text = task.lower()
    library = cfg.get("skill_library") or {}
    daily_ids = set(profile.get("daily_skills") or [])
    daily = []
    contextual = []

    for item in library.get("daily", []):
        skill = _skill_item(item)
        if skill["id"] in daily_ids or _keywords_match(text, skill["keywords"]):
            daily.append(skill)

    for item in library.get("library", []):
        skill = _skill_item(item)
        if skill["id"] in daily_ids:
            daily.append(skill)
        elif _keywords_match(text, skill["keywords"]):
            contextual.append(skill)

    return {"daily": _unique_skills(daily), "library": _unique_skills(contextual)}


def _normalize_config(data: dict[str, Any]) -> dict[str, Any]:
    agents = []
    for raw in data.get("agents", []):
        if not raw.get("id"):
            continue
        name = raw.get("name") or raw["id"]
        agents.append(
            {
                "id": str(raw["id"]),
                "name": str(name),
                "app": str(raw.get("app") or name),
                "description": str(raw.get("description") or ""),
                "keywords": [str(item).lower() for item in raw.get("keywords", [])],
                "prompt_role": str(raw.get("prompt_role") or f"You are {name}. Work on tasks matching your role."),
            }
        )
    if not agents:
        return _fallback_config()

    ids = {agent["id"] for agent in agents}
    default_owner = data.get("default_owner") if data.get("default_owner") in ids else agents[0]["id"]
    prompt = data.get("prompt") or {}
    return {
        "default_owner": default_owner,
        "agents": agents,
        "rules": data.get("rules", []),
        "role_presets": data.get("role_presets", {}),
        "skill_library": data.get("skill_library", {}),
        "prompt": {
            "constraints": [str(item) for item in prompt.get("constraints", [])],
            "deliverables": [str(item) for item in prompt.get("deliverables", [])],
        },
    }


def _normalize_profiles(data: dict[str, Any]) -> dict[str, Any]:
    raw_profiles = data.get("profiles") or {}
    profiles = {}
    for name, profile in raw_profiles.items():
        profiles[str(name)] = {
            "id": str(name),
            "description": str(profile.get("description") or ""),
            "daily_skills": [str(item) for item in profile.get("daily_skills", [])],
            "roles": [str(item) for item in profile.get("roles", [])],
            "constraints": [str(item) for item in profile.get("constraints", [])],
        }
    if not profiles:
        return _fallback_profiles()
    default = str(data.get("default") or next(iter(profiles)))
    if default not in profiles:
        default = next(iter(profiles))
    return {"default": default, "profiles": profiles}


def _skill_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item.get("id") or "unnamed"),
        "description": str(item.get("description") or ""),
        "keywords": [str(keyword).lower() for keyword in item.get("keywords", [])],
    }


def _keywords_match(text: str, keywords: list[str]) -> bool:
    return any(keyword and keyword in text for keyword in keywords)


def _unique_skills(skills: list[dict[str, Any]]) -> list[dict[str, str]]:
    seen = set()
    result = []
    for skill in skills:
        if skill["id"] in seen:
            continue
        seen.add(skill["id"])
        result.append({"id": skill["id"], "description": skill["description"]})
    return result


def _fallback_profiles() -> dict[str, Any]:
    return {
        "default": "developer",
        "profiles": {
            "developer": {
                "id": "developer",
                "description": "Default engineering profile.",
                "daily_skills": ["verification-loop"],
                "roles": ["explorer", "fixer", "reviewer"],
                "constraints": ["Use evidence from the current repo before routing."],
            }
        },
    }


def _fallback_config() -> dict[str, Any]:
    return {
        "default_owner": "codex",
        "agents": [
            {
                "id": "codex",
                "name": "Codex",
                "app": "Codex",
                "description": "General repo inspection and debugging.",
                "keywords": ["debug", "review", "architecture", "payment", "backend", "frontend"],
                "prompt_role": "You are Codex. Inspect the repo, reason cross-stack, and keep changes safe.",
            }
        ],
        "rules": [],
        "prompt": {
            "constraints": [
                "Do not use production for testing.",
                "Do not run destructive commands.",
                "Preserve existing user changes.",
            ],
            "deliverables": [
                "What you changed or found.",
                "Verification result.",
                "Any remaining blocker or risk.",
            ],
        },
    }
