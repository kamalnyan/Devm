"""ECC (Everything Claude Code) integration.

Auto-detects ECC framework in a repo and loads its agents/skills into
the Devm council pipeline. When a project has agent.yaml or skills/,
the relevant skill content is injected into handoff prompts.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any


# ECC markers — if any of these exist in a repo, it's an ECC project
_ECC_MARKERS = ("agent.yaml", "AGENTS.md", "SOUL.md", "RULES.md")
_SKILL_FILE_NAMES = ("SKILL.md", "README.md", "skill.md")

# Keyword → skill name mapping for fast matching
_SKILL_KEYWORDS: dict[str, list[str]] = {
    "security":      ["security-review", "security-first", "vulnerability"],
    "backend":       ["backend-patterns", "api-design", "nestjs"],
    "frontend":      ["frontend", "react", "flutter", "compose-multiplatform-patterns"],
    "test":          ["tdd", "testing", "verification-loop", "ai-regression-testing"],
    "database":      ["database", "prisma", "postgres", "redis"],
    "payment":       ["payment", "razorpay", "stripe", "agent-payment-x402"],
    "docker":        ["docker", "devops", "ci"],
    "review":        ["code-review", "code-reviewer"],
    "refactor":      ["refactor", "clean"],
    "architecture":  ["architecture", "architect", "design"],
    "auth":          ["auth", "jwt", "oauth"],
    "api":           ["api-design", "rest", "graphql"],
    "android":       ["android-clean-architecture", "kotlin"],
    "rust":          ["rust"],
    "python":        ["python"],
    "go":            ["go"],
}


def detect_ecc(repo_root: Path) -> dict[str, Any] | None:
    """Return ECC metadata if the repo uses ECC, else None."""
    root = Path(repo_root).expanduser().resolve()
    found_marker = next((m for m in _ECC_MARKERS if (root / m).exists()), None)
    if not found_marker:
        return None

    info: dict[str, Any] = {
        "marker": found_marker,
        "root": str(root),
        "model_preferred": None,
        "model_fallback": [],
        "skills_dir": None,
        "agents_dir": None,
        "available_skills": [],
    }

    # Read agent.yaml for model + skill list
    agent_yaml = root / "agent.yaml"
    if agent_yaml.exists():
        cfg = _parse_yaml_simple(agent_yaml)
        model_cfg = cfg.get("model", {})
        info["model_preferred"] = model_cfg.get("preferred")
        info["model_fallback"] = model_cfg.get("fallback", [])
        info["available_skills"] = cfg.get("skills", [])

    # Detect skills directory
    for sd in ("skills", ".agents/skills", "agents/skills"):
        d = root / sd
        if d.is_dir():
            info["skills_dir"] = str(d)
            if not info["available_skills"]:
                info["available_skills"] = [p.name for p in d.iterdir() if p.is_dir()]
            break

    # Detect agents directory
    for ad in ("agents", ".agents", ".claude/agents"):
        d = root / ad
        if d.is_dir():
            info["agents_dir"] = str(d)
            break

    return info


def match_skills(task: str, ecc_info: dict[str, Any], max_skills: int = 3) -> list[str]:
    """Return ECC skill names relevant to the task (by keyword matching)."""
    task_lower = task.lower()
    available = set(ecc_info.get("available_skills", []))
    matched: list[str] = []

    for keyword, skill_hints in _SKILL_KEYWORDS.items():
        if keyword in task_lower:
            for hint in skill_hints:
                # Find any available skill whose name contains the hint
                for skill in available:
                    if hint in skill and skill not in matched:
                        matched.append(skill)
                        break
            if len(matched) >= max_skills:
                break

    return matched[:max_skills]


def load_skill_content(skill_name: str, ecc_info: dict[str, Any], max_chars: int = 1200) -> str | None:
    """Read a skill's SKILL.md content from the ECC skills directory."""
    skills_dir = ecc_info.get("skills_dir")
    if not skills_dir:
        return None
    skill_path = Path(skills_dir) / skill_name
    for fname in _SKILL_FILE_NAMES:
        f = skill_path / fname
        if f.exists():
            text = f.read_text(errors="replace")
            return text[:max_chars] + ("…" if len(text) > max_chars else "")
    return None


def build_ecc_context_block(task: str, ecc_info: dict[str, Any]) -> str:
    """Build a text block with ECC skill content to inject into handoff prompts."""
    skills = match_skills(task, ecc_info)
    if not skills:
        return ""

    parts = [f"# ECC Framework — matched skills for this task\n"]
    for skill_name in skills:
        content = load_skill_content(skill_name, ecc_info)
        if content:
            parts.append(f"## Skill: {skill_name}\n{content}\n")

    return "\n".join(parts) if len(parts) > 1 else ""


def suggested_model(ecc_info: dict[str, Any]) -> str | None:
    """Return the ECC-preferred model name (e.g. claude-opus-4-6), if set."""
    return ecc_info.get("model_preferred")


def _parse_yaml_simple(path: Path) -> dict:
    """Minimal YAML parser for agent.yaml — handles key: value and lists."""
    try:
        import yaml  # type: ignore[import]
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        pass
    # Fallback: line-by-line parse for simple structures
    result: dict = {}
    try:
        lines = path.read_text().splitlines()
        current_list_key: str | None = None
        for line in lines:
            stripped = line.rstrip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("  - ") and current_list_key:
                result.setdefault(current_list_key, []).append(stripped[4:].strip())
            elif ":" in stripped and not stripped.startswith(" "):
                k, _, v = stripped.partition(":")
                v = v.strip()
                if v:
                    result[k.strip()] = v
                    current_list_key = None
                else:
                    current_list_key = k.strip()
                    result.setdefault(current_list_key, [])
    except Exception:
        pass
    return result
