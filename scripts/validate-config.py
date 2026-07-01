#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    errors = []
    agents = _read_json(ROOT / "config" / "agents.json", errors)
    profiles = _read_json(ROOT / "config" / "profiles.json", errors)

    agent_ids = set()
    if agents:
        for index, agent in enumerate(agents.get("agents", [])):
            agent_id = agent.get("id")
            if not agent_id:
                errors.append(f"agents[{index}] missing id")
                continue
            agent_ids.add(agent_id)
            for field in ("name", "keywords", "prompt_role"):
                if field not in agent:
                    errors.append(f"agent {agent_id} missing {field}")
        if agents.get("default_owner") not in agent_ids:
            errors.append("default_owner must match an agent id")

    role_ids = set((agents or {}).get("role_presets", {}).keys())
    skill_ids = set()
    library = (agents or {}).get("skill_library", {})
    for bucket in ("daily", "library"):
        for item in library.get(bucket, []):
            if item.get("id"):
                skill_ids.add(item["id"])

    if profiles:
        profile_map = profiles.get("profiles", {})
        if profiles.get("default") not in profile_map:
            errors.append("profiles.default must match a profile name")
        for name, profile in profile_map.items():
            for role in profile.get("roles", []):
                if role not in role_ids:
                    errors.append(f"profile {name} references unknown role {role}")
            for skill in profile.get("daily_skills", []):
                if skill not in skill_ids:
                    errors.append(f"profile {name} references unknown skill {skill}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("config validation passed")
    return 0


def _read_json(path: Path, errors: list[str]) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{path}: {exc}")
        return {}


if __name__ == "__main__":
    raise SystemExit(main())
