"""Natural language skill and agent recommender — like ECC's consult.js."""
from __future__ import annotations

import re
from pathlib import Path


SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"
AGENTS_DIR = Path(__file__).resolve().parents[1] / "agents"


def consult(query: str, top_n: int = 5) -> dict:
    """Given a natural language query, recommend matching skills and agents."""
    skills = _scan_skills()
    agents = _scan_agents()

    query_terms = _tokenize(query)
    scored_skills = _score(skills, query_terms)
    scored_agents = _score(agents, query_terms)

    return {
        "query": query,
        "skills": scored_skills[:top_n],
        "agents": scored_agents[:top_n],
    }


def print_consult(query: str) -> None:
    BOLD = "\033[1m"; DIM = "\033[2m"; GREEN = "\033[32m"
    CYAN = "\033[36m"; YELLOW = "\033[33m"; RESET = "\033[0m"

    result = consult(query)
    print(f"\n{BOLD}╭─ Consult: \"{result['query']}\" {'─' * max(0, 38 - len(result['query']))}╮{RESET}")

    if result["skills"]:
        print(f"{BOLD}│{RESET}  {CYAN}Skills matched:{RESET}")
        for item in result["skills"][:5]:
            bar = "█" * item["score"] + "░" * max(0, 5 - item["score"])
            print(f"{BOLD}│{RESET}  {GREEN}{bar}{RESET}  {BOLD}{item['name']}{RESET}")
            print(f"{BOLD}│{RESET}       {DIM}{item['description']}{RESET}")
            role_hint = _slug(item["name"])
            print(f"{BOLD}│{RESET}       {DIM}$ devm --role {role_hint} \"your task\"{RESET}")
    else:
        print(f"{BOLD}│{RESET}  {DIM}No skills matched. Try broader keywords.{RESET}")

    print(f"{BOLD}│{RESET}")

    if result["agents"]:
        print(f"{BOLD}│{RESET}  {YELLOW}Agents matched:{RESET}")
        for item in result["agents"][:3]:
            routes = item.get("routes_to", "?")
            print(f"{BOLD}│{RESET}  {BOLD}{item['name']}{RESET}  {DIM}→ {routes}{RESET}")
            print(f"{BOLD}│{RESET}       {DIM}{item['description']}{RESET}")
    else:
        print(f"{BOLD}│{RESET}  {DIM}No agents matched.{RESET}")

    print(f"{BOLD}╰{'─' * 52}╯{RESET}")
    print(f"\n  {DIM}devm --list-agents   devm --list-roles   devm --list-profiles{RESET}\n")


def _scan_skills() -> list[dict]:
    items = []
    if not SKILLS_DIR.exists():
        return items
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            continue
        meta = _parse_frontmatter(skill_file.read_text(encoding="utf-8"))
        items.append({
            "name": meta.get("name") or skill_dir.name,
            "description": meta.get("description") or "",
            "keywords": _parse_list(meta.get("keywords") or ""),
            "path": str(skill_file),
        })
    return items


def _scan_agents() -> list[dict]:
    items = []
    if not AGENTS_DIR.exists():
        return items
    for agent_file in sorted(AGENTS_DIR.glob("*.md")):
        meta = _parse_frontmatter(agent_file.read_text(encoding="utf-8"))
        items.append({
            "name": meta.get("name") or agent_file.stem,
            "description": meta.get("description") or "",
            "keywords": _parse_list(meta.get("keywords") or ""),
            "routes_to": meta.get("routes_to") or "",
            "path": str(agent_file),
        })
    return items


def _score(items: list[dict], query_terms: list[str]) -> list[dict]:
    scored = []
    for item in items:
        item_terms = _tokenize(item["description"]) + item.get("keywords", [])
        score = sum(1 for qt in query_terms if any(qt in it for it in item_terms))
        if score > 0:
            scored.append({**item, "score": score})
    return sorted(scored, key=lambda x: x["score"], reverse=True)


def _tokenize(text: str) -> list[str]:
    return [w.lower() for w in re.findall(r"[a-zA-Z0-9]+", text) if len(w) > 2]


def _parse_frontmatter(content: str) -> dict:
    """Extract YAML frontmatter fields (simple key: value, no nesting)."""
    meta: dict = {}
    if not content.startswith("---"):
        return meta
    end = content.find("---", 3)
    if end == -1:
        return meta
    block = content[3:end]
    for line in block.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta


def _parse_list(value: str) -> list[str]:
    """Parse '[a, b, c]' or 'a, b, c' into a list."""
    clean = value.strip().strip("[]")
    return [item.strip().strip("\"'") for item in clean.split(",") if item.strip()]


def _slug(name: str) -> str:
    return name.replace(" ", "-").lower()
