"""Autonomous solver — sends handoff prompt directly to LLM and streams the answer.

This replaces the GUI paste step: instead of opening Claude/Codex, we call the
configured LLM provider directly and return the response in the terminal (or save
it to a job file for background runs).
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from typing import Any, Iterator

from .user_config import PROVIDERS, litellm_model_string, load as load_config


# System prompt that turns the LLM into the assigned AI agent
_SYSTEM = """You are an expert software engineering assistant acting as an autonomous dev agent.
You have been given a detailed task brief including:
- The user's task description
- Skill guidance and checklists
- Relevant code found in the repo
- Project structure and safe verification commands

Your job:
1. Analyze the task and the code evidence provided.
2. Give a concrete, actionable response: root cause analysis, code changes needed, commands to run.
3. Be specific — reference actual file paths and line hints from the search results.
4. Do NOT invent file paths or code that wasn't in the evidence.
5. Keep your answer focused and practical. Use markdown.
"""


def solve(
    handoff_prompt: str,
    cfg: dict[str, Any] | None = None,
    stream: bool = True,
) -> str:
    """Send the handoff prompt to the configured LLM and return the full response.

    If stream=True (default), prints tokens to stdout as they arrive.
    Returns the complete response text regardless.
    """
    if cfg is None:
        cfg = load_config()

    provider = cfg.get("provider", "ollama")

    if provider == "ollama":
        return _solve_ollama(handoff_prompt, cfg, stream=stream)
    return _solve_litellm(handoff_prompt, cfg, stream=stream)


# ─────────────────────────────────────────────────────────────────────────────
# Ollama — direct HTTP with streaming
# ─────────────────────────────────────────────────────────────────────────────

def _solve_ollama(prompt: str, cfg: dict[str, Any], stream: bool) -> str:
    url = cfg.get("ollama_url", "http://127.0.0.1:11434")
    model = cfg.get("model", "glm4:latest")

    full_prompt = f"[SYSTEM]\n{_SYSTEM}\n\n[TASK BRIEF]\n{prompt}"

    body = json.dumps({
        "model": model,
        "prompt": full_prompt,
        "stream": True,
        "options": {"temperature": 0.2, "num_ctx": 8192},
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{url.rstrip('/')}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    collected: list[str] = []
    try:
        with urllib.request.urlopen(req, timeout=cfg.get("timeout", 120)) as resp:
            for chunk in _iter_ndjson(resp):
                token = chunk.get("response", "")
                if token:
                    collected.append(token)
                    if stream:
                        print(token, end="", flush=True)
                if chunk.get("done"):
                    break
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        msg = f"\n[solver] Ollama error: {exc}\n  Is ollama running? Run: ollama serve"
        print(msg, file=sys.stderr)
        return ""

    if stream:
        print()  # final newline
    return "".join(collected)


def _iter_ndjson(resp) -> Iterator[dict]:
    """Iterate newline-delimited JSON lines from an HTTP response."""
    buf = b""
    while True:
        chunk = resp.read(256)
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    pass


# ─────────────────────────────────────────────────────────────────────────────
# LiteLLM — paid/cloud providers (OpenAI, Anthropic, Gemini, Groq, Together…)
# ─────────────────────────────────────────────────────────────────────────────

def _solve_litellm(prompt: str, cfg: dict[str, Any], stream: bool) -> str:
    try:
        from litellm import completion  # type: ignore[import]
    except ImportError:
        print("[solver] litellm not installed. Run: pip install litellm", file=sys.stderr)
        return ""

    provider = cfg.get("provider", "openai")
    info = PROVIDERS.get(provider, {})
    model_str = litellm_model_string(cfg)
    api_key = cfg.get("api_key") or "none"
    base_url = cfg.get("base_url") if provider in ("ollama", "openai-compatible") else None

    kwargs: dict[str, Any] = {
        "model": model_str,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 4096,
        "stream": stream,
        "timeout": cfg.get("timeout", 120),
    }
    if api_key and api_key != "none":
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["api_base"] = base_url

    collected: list[str] = []
    try:
        response = completion(**kwargs)
        if stream:
            for chunk in response:
                token = (chunk.choices[0].delta.content or "") if chunk.choices else ""
                if token:
                    collected.append(token)
                    print(token, end="", flush=True)
            print()
        else:
            text = response.choices[0].message.content or ""
            collected.append(text)
    except Exception as exc:
        env_key = info.get("env_key")
        print(f"\n[solver] {provider} error: {exc}", file=sys.stderr)
        if env_key:
            print(f"  Check: export {env_key}=your-key", file=sys.stderr)
        return ""

    return "".join(collected)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def check_provider(cfg: dict[str, Any] | None = None) -> tuple[bool, str]:
    """Quick reachability check. Returns (ok, message)."""
    if cfg is None:
        cfg = load_config()
    provider = cfg.get("provider", "ollama")
    if provider == "ollama":
        url = cfg.get("ollama_url", "http://127.0.0.1:11434")
        try:
            req = urllib.request.Request(f"{url}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=5):
                return True, f"Ollama reachable at {url}"
        except Exception as exc:
            return False, f"Ollama not reachable ({exc}). Run: ollama serve"
    # For cloud providers just check the key is set
    info = PROVIDERS.get(provider, {})
    if info.get("needs_key") and not cfg.get("api_key"):
        env_key = info.get("env_key", "API_KEY")
        return False, f"API key not set for {provider}. export {env_key}=..."
    return True, f"{provider} configured (key present)"
