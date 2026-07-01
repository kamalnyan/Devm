"""Universal LLM caller — supports Ollama, OpenAI, Anthropic, Gemini, Groq, Together, etc.

Uses LiteLLM as the bridge when available, falls back to direct Ollama HTTP for local models.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .user_config import PROVIDERS, litellm_model_string, load as load_config


def ask(
    task: str,
    context: dict,
    local_route: dict,
    cfg: dict[str, Any] | None = None,
) -> dict | None:
    """Route a task through the configured LLM provider. Returns route dict or None.

    Trust policy:
    - Local rules HIGH confidence + score gap ≥ 2 → skip LLM, trust local
    - Otherwise → call LLM as tiebreaker/enhancer
    """
    if cfg is None:
        cfg = load_config()

    # Skip LLM when local rules are already decisive
    if _local_rules_decisive(local_route):
        return None  # caller uses local_route directly

    provider = cfg.get("provider", "ollama")
    prompt = _build_prompt(task, context, local_route)

    if provider == "ollama":
        return _ask_ollama_direct(prompt, cfg)
    return _ask_litellm(prompt, cfg)


def _ask_litellm(prompt: str, cfg: dict[str, Any]) -> dict | None:
    try:
        from litellm import completion  # type: ignore[import]
    except ImportError:
        print("LiteLLM not installed. Run: pip install litellm")
        return None

    provider = cfg.get("provider", "openai")
    info = PROVIDERS.get(provider, {})
    model_str = litellm_model_string(cfg)
    api_key = cfg.get("api_key") or "none"
    base_url = cfg.get("base_url") if provider in ("ollama", "openai-compatible") else None

    try:
        kwargs: dict[str, Any] = {
            "model": model_str,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 256,
            "timeout": cfg.get("timeout", 45),
        }
        if api_key and api_key != "none":
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["api_base"] = base_url

        response = completion(**kwargs)
        raw = response.choices[0].message.content or ""
    except Exception as exc:
        _print_provider_hint(provider, info, exc)
        return None

    return _parse_response(raw)


def _ask_ollama_direct(prompt: str, cfg: dict[str, Any]) -> dict | None:
    """Direct HTTP call to Ollama — no LiteLLM needed."""
    url = cfg.get("ollama_url", "http://127.0.0.1:11434")
    model = cfg.get("model", "glm4:latest")
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.1, "num_ctx": 4096},
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{url.rstrip('/')}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg.get("timeout", 45)) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None

    raw = (payload.get("response") or "").strip()
    return _parse_response(raw)


def _build_prompt(task: str, context: dict, local_route: dict) -> str:
    scripts: dict = {}
    for name, project in context.get("projects", {}).items():
        scripts[name] = (project.get("package") or {}).get("scripts", {})
    return f"""You are a local development manager. Route this task to exactly one owner:
- frontend: for UI/React/Next.js/Flutter/CSS/component work
- backend: for API/NestJS/Prisma/Docker/Redis/backend implementation
- codex: for architecture, debugging, review, logs, CI, release, cross-stack work

Return ONLY valid JSON, nothing else:
{{"owner":"frontend|backend|codex","confidence":"low|medium|high","reason":"short reason"}}

Task:
{task}

Available project scripts:
{json.dumps(scripts, indent=2)}

Local rules suggestion:
{json.dumps({"owner": local_route.get("owner"), "scores": local_route.get("scores", {})}, indent=2)}
"""


def _parse_response(raw: str) -> dict | None:
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return None

    owner = parsed.get("owner")
    if owner not in {"frontend", "backend", "codex"}:
        return None

    return {
        "owner": owner,
        "target_app": {"frontend": "Antigravity", "backend": "Claude", "codex": "Codex"}[owner],
        "confidence": parsed.get("confidence", "medium"),
        "reason": parsed.get("reason", "LLM routing."),
        "source": "llm",
    }


def _local_rules_decisive(local_route: dict) -> bool:
    """Return True when local rules are confident enough to skip LLM."""
    if local_route.get("confidence") != "high":
        return False
    scores = local_route.get("scores", {})
    if len(scores) < 2:
        return True
    sorted_scores = sorted(scores.values(), reverse=True)
    # Trust local rules when top score leads by ≥ 2 points
    return (sorted_scores[0] - sorted_scores[1]) >= 2


def _print_provider_hint(provider: str, info: dict, exc: Exception) -> None:
    env_key = info.get("env_key")
    print(f"[devmanager] LLM call failed ({provider}): {exc}")
    if env_key:
        print(f"  Check: export {env_key}=your-key-here")
    print("  Or switch to local: devm config set provider=ollama model=glm4:latest")
    print("  Falling back to local routing rules.")
