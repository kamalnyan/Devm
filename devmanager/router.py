from __future__ import annotations

import re

from devmanager.agent_config import agent_for_owner, agents_by_id, load_agent_config


def classify_task(task: str, context: dict) -> dict:
    config = load_agent_config()
    text = task.lower()
    agents = agents_by_id(config)
    scores = {owner: 0 for owner in agents}
    applied_rules = []

    for owner, agent in agents.items():
        _score_terms(text, scores, owner, agent.get("keywords", []))

    for rule in config.get("rules", []):
        if _rule_matches(text, rule):
            owner = rule.get("owner")
            if owner in scores:
                scores[owner] += int(rule.get("boost") or 0)
                applied_rules.append(rule)

    if "frontend" in context.get("projects", {}) and any(term in text for term in ["next", "react"]):
        _boost_if_present(scores, "frontend", 2)
    if "backend" in context.get("projects", {}) and any(term in text for term in ["nestjs", "prisma", "redis"]):
        _boost_if_present(scores, "backend", 2)

    owner = max(scores, key=scores.get)
    if applied_rules:
        strongest = sorted(applied_rules, key=lambda item: int(item.get("boost") or 0), reverse=True)[0]
        owner = strongest.get("owner", owner)
        confidence = strongest.get("confidence") or "high"
        reason = f"{strongest.get('reason', 'Configured routing rule matched')} Keyword routing scores: {scores}."
    elif scores[owner] == 0:
        owner = config["default_owner"]
        confidence = "low"
        reason = f"No strong owner keywords found; {agent_for_owner(owner, config)['name']} should inspect and route further."
    else:
        sorted_scores = sorted(scores.values(), reverse=True)
        confidence = "high" if sorted_scores[0] >= sorted_scores[1] + 3 else "medium"
        reason = f"Keyword routing scores: {scores}."

    agent = agent_for_owner(owner, config)
    return {
        "owner": owner,
        "target_app": agent.get("app") or agent["name"],
        "target_name": agent["name"],
        "confidence": confidence,
        "reason": reason,
        "scores": scores,
        "source": "local-rules",
    }


def _score_terms(text: str, scores: dict, owner: str, terms: list[str]) -> None:
    for term in terms:
        if term in text:
            scores[owner] += 1


def _rule_matches(text: str, rule: dict) -> bool:
    return all(_pattern_group_matches(text, group) for group in rule.get("when_all", []))


def _pattern_group_matches(text: str, group: str) -> bool:
    for raw in str(group).split("|"):
        term = raw.strip().lower()
        if term and re.search(re.escape(term), text):
            return True
    return False


def _boost_if_present(scores: dict, owner: str, amount: int) -> None:
    if owner in scores:
        scores[owner] += amount
