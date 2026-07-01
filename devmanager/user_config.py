"""Persistent user config at ~/.devmanager/config.json."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


CONFIG_PATH = Path.home() / ".devmanager" / "config.json"

# Supported providers and their defaults
PROVIDERS: dict[str, dict[str, Any]] = {
    "ollama": {
        "label": "Ollama (local, free, no API key)",
        "model": "glm4:latest",
        "litellm_prefix": "ollama",
        "needs_key": False,
        "env_key": None,
        "base_url_default": "http://127.0.0.1:11434",
    },
    "openai": {
        "label": "OpenAI (GPT-4o, GPT-4o-mini, paid)",
        "model": "gpt-4o-mini",
        "litellm_prefix": "openai",
        "needs_key": True,
        "env_key": "OPENAI_API_KEY",
        "base_url_default": None,
    },
    "anthropic": {
        "label": "Anthropic Claude (claude-3-5-haiku, paid)",
        "model": "claude-3-5-haiku-20241022",
        "litellm_prefix": "anthropic",
        "needs_key": True,
        "env_key": "ANTHROPIC_API_KEY",
        "base_url_default": None,
    },
    "gemini": {
        "label": "Google Gemini (gemini-1.5-flash, free tier available)",
        "model": "gemini-1.5-flash",
        "litellm_prefix": "gemini",
        "needs_key": True,
        "env_key": "GOOGLE_API_KEY",
        "base_url_default": None,
    },
    "groq": {
        "label": "Groq (llama3, fast free tier)",
        "model": "llama-3.1-8b-instant",
        "litellm_prefix": "groq",
        "needs_key": True,
        "env_key": "GROQ_API_KEY",
        "base_url_default": None,
    },
    "together": {
        "label": "Together AI (open models, paid)",
        "model": "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
        "litellm_prefix": "together_ai",
        "needs_key": True,
        "env_key": "TOGETHER_API_KEY",
        "base_url_default": None,
    },
    "openai-compatible": {
        "label": "OpenAI-compatible (custom base URL — LM Studio, vLLM, etc.)",
        "model": "local-model",
        "litellm_prefix": "openai",
        "needs_key": False,
        "env_key": "OPENAI_API_KEY",
        "base_url_default": "http://localhost:1234/v1",
    },
}

_DEFAULTS = {
    "provider": "ollama",
    "model": "glm4:latest",
    "api_key": None,
    "base_url": "http://127.0.0.1:11434",
    "ollama_url": "http://127.0.0.1:11434",
    "timeout": 45,
}


def load() -> dict[str, Any]:
    try:
        stored = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        stored = {}
    cfg = {**_DEFAULTS, **stored}
    # Env vars override config file (but not CLI flags)
    provider = cfg["provider"]
    info = PROVIDERS.get(provider, {})
    env_key = info.get("env_key")
    if env_key and os.getenv(env_key):
        cfg["api_key"] = os.getenv(env_key)
    return cfg


def save(updates: dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        current = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        current = {}
    current.update(updates)
    CONFIG_PATH.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")


def provider_info(provider: str | None = None) -> dict[str, Any]:
    cfg = load()
    name = provider or cfg["provider"]
    return PROVIDERS.get(name, PROVIDERS["ollama"])


def litellm_model_string(cfg: dict[str, Any]) -> str:
    """Return the LiteLLM model string for the configured provider."""
    provider = cfg.get("provider", "ollama")
    model = cfg.get("model", "glm4:latest")
    info = PROVIDERS.get(provider, PROVIDERS["ollama"])
    prefix = info["litellm_prefix"]

    # ollama models already have ':latest' suffix — strip for litellm if needed
    if provider == "ollama":
        return f"ollama/{model}"

    return f"{prefix}/{model}"


def print_config() -> None:
    BOLD = "\033[1m"; DIM = "\033[2m"; GREEN = "\033[32m"; YELLOW = "\033[33m"
    CYAN = "\033[36m"; RED = "\033[31m"; RESET = "\033[0m"

    cfg = load()
    provider = cfg["provider"]
    info = PROVIDERS.get(provider, {})
    key = cfg.get("api_key")

    print(f"\n{BOLD}╭─ DevManager Config {'─' * 32}╮{RESET}")
    print(f"{BOLD}│{RESET}  Provider  {CYAN}{BOLD}{provider}{RESET}  {DIM}{info.get('label', '')}{RESET}")
    print(f"{BOLD}│{RESET}  Model     {BOLD}{cfg['model']}{RESET}")
    if cfg.get("base_url") and provider in ("ollama", "openai-compatible"):
        print(f"{BOLD}│{RESET}  Base URL  {cfg['base_url']}")
    if key:
        masked = "*" * max(0, len(key) - 4) + key[-4:]
        print(f"{BOLD}│{RESET}  API Key   {GREEN}✓ set{RESET}  {DIM}({masked}){RESET}")
    elif info.get("needs_key"):
        env = info.get("env_key", "")
        print(f"{BOLD}│{RESET}  API Key   {RED}✗ not set{RESET}  {DIM}export {env}=... or devm config set api_key=...{RESET}")
    else:
        print(f"{BOLD}│{RESET}  API Key   {DIM}not required{RESET}")
    print(f"{BOLD}│{RESET}  {DIM}Config: {CONFIG_PATH}{RESET}")
    print(f"{BOLD}╰{'─' * 52}╯{RESET}")

    print(f"\n{BOLD}Available providers:{RESET}")
    for name, p in PROVIDERS.items():
        if name == provider:
            print(f"  {GREEN}▶  {name:<22}{RESET} {p['label']}")
        else:
            print(f"  {DIM}   {name:<22}{RESET} {DIM}{p['label']}{RESET}")

    print(f"\n  {DIM}devm config set provider=openai model=gpt-4o-mini{RESET}")
    print(f"  {DIM}devm config set provider=ollama model=llama3.2{RESET}")
    print(f"  {DIM}devm config set api_key=sk-...{RESET}")
    print(f"  {DIM}devm config reset{RESET}\n")
