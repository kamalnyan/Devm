from __future__ import annotations

import json
import urllib.error
import urllib.request


def ask_ollama(url: str, model: str, task: str, context: dict, local_route: dict) -> dict | None:
    prompt = _build_prompt(task, context, local_route)
    body = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1, "num_ctx": 4096},
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        f"{url.rstrip('/')}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None

    raw = (payload.get("response") or "").strip()
    parsed = _extract_json(raw)
    if not parsed:
        return None

    owner = parsed.get("owner")
    if owner not in {"frontend", "backend", "codex"}:
        return None

    return {
        "owner": owner,
        "target_app": {"frontend": "Antigravity", "backend": "Claude", "codex": "Codex"}[owner],
        "confidence": parsed.get("confidence", "medium"),
        "reason": parsed.get("reason", "Local model routing."),
        "scores": local_route.get("scores", {}),
        "source": f"ollama:{model}",
    }


def _build_prompt(task: str, context: dict, local_route: dict) -> str:
    scripts = {}
    for name, project in context.get("projects", {}).items():
        scripts[name] = (project.get("package") or {}).get("scripts", {})

    return f"""You are a local development manager. No paid AI APIs, no external app CLIs.
Route this task to exactly one owner:
- frontend: Antigravity, for UI/React/Next/CSS/component work
- backend: Claude, for API/NestJS/Prisma/Docker/backend implementation
- codex: Codex, for architecture, debugging, review, logs, CI, release, cross-stack work

Return only JSON:
{{"owner":"frontend|backend|codex","confidence":"low|medium|high","reason":"short reason"}}

Task:
{task}

Available scripts:
{json.dumps(scripts, indent=2)}

Local rules suggestion:
{json.dumps(local_route, indent=2)}
"""


def _extract_json(raw: str) -> dict | None:
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None

