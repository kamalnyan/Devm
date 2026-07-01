"""Agent Bridge — unlimited, plugin-style AI agent discovery and execution.

Any AI CLI tool can be registered here. Discovery is automatic:
  - Built-in registry covers known tools (claude, codex, aider, gemini, cursor, etc.)
  - Custom agents can be added via ~/.devmanager/agents.json
  - Any unknown binary that responds to --version is usable

Adding a new agent:
  devm agent-add   (interactive — scans machine, pick from list)

Built-in known agents (auto-discovered if installed):
  claude    → Claude Code CLI
  codex     → Codex CLI (ChatGPT)
  aider     → Aider (open-source, works with any LLM)
  gemini    → Google Gemini CLI
  cursor    → Cursor CLI (if available)
  ollama    → Ollama local models (via solver)
  opencode  → OpenCode CLI
  continue  → Continue.dev CLI
  gh-copilot → GitHub Copilot CLI
  goose     → Block's Goose agent
  amp       → Sourcegraph Amp
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# Built-in agent registry — each entry describes how to call the agent
# ─────────────────────────────────────────────────────────────────────────────

# "stdin": True  → prompt via stdin (cmd ends with "-" or no prompt arg)
# "stdin": False → prompt as positional arg
# "auth_check": callable key → how to verify it's logged in
# "cmd_builder": key → which _build_* function to use

_BUILTIN_REGISTRY: dict[str, dict] = {
    "claude": {
        "name": "Claude Code",
        "search_paths": [
            # versioned — glob at runtime
            "__CLAUDE_VERSIONED__",
            "/Applications/Claude.app/Contents/Resources/claude",
        ],
        "path_env": "CLAUDE_CLI",
        "stdin": False,
        "cmd_template": ["{binary}", "-p", "{prompt}", "--output-format", "text"],
        "auth_marker_fail": ["not logged in", "please run /login"],
        "auth_cmd": ["{binary}", "-p", "ping", "--output-format", "text"],
        "strengths": ["backend", "implementation", "debugging", "refactor"],
        "timeout": 300,
    },
    "codex": {
        "name": "Codex CLI",
        "search_paths": [
            "/Applications/Codex.app/Contents/Resources/codex",
        ],
        "path_env": "CODEX_CLI",
        "stdin": True,
        "cmd_template": ["{binary}", "exec", "-c", 'sandbox_permissions=["disk-full-read-access"]', "-"],
        "auth_marker_fail": ["not authenticated", "login required"],
        "auth_cmd": ["{binary}", "doctor"],
        "strengths": ["exploration", "review", "architecture", "cross-stack"],
        "timeout": 300,
    },
    "aider": {
        "name": "Aider",
        "search_paths": [],
        "path_env": "AIDER_CLI",
        "stdin": False,
        "cmd_template": ["{binary}", "--message", "{prompt}", "--no-git", "--yes"],
        "auth_marker_fail": [],
        "auth_cmd": ["{binary}", "--version"],
        "strengths": ["implementation", "refactor", "multi-file"],
        "timeout": 300,
    },
    "gemini": {
        "name": "Gemini CLI",
        "search_paths": [],
        "path_env": "GEMINI_CLI",
        "stdin": False,
        "cmd_template": ["{binary}", "-p", "{prompt}"],
        "auth_marker_fail": ["not logged in", "auth", "login"],
        "auth_cmd": ["{binary}", "--version"],
        "strengths": ["research", "frontend", "multi-modal"],
        "timeout": 120,
    },
    "opencode": {
        "name": "OpenCode",
        "search_paths": [],
        "path_env": "OPENCODE_CLI",
        "stdin": True,
        "cmd_template": ["{binary}", "run", "-"],
        "auth_marker_fail": [],
        "auth_cmd": ["{binary}", "--version"],
        "strengths": ["backend", "implementation"],
        "timeout": 300,
    },
    "goose": {
        "name": "Goose (Block)",
        "search_paths": [],
        "path_env": "GOOSE_CLI",
        "stdin": False,
        "cmd_template": ["{binary}", "run", "--text", "{prompt}"],
        "auth_marker_fail": [],
        "auth_cmd": ["{binary}", "--version"],
        "strengths": ["automation", "devops", "cross-stack"],
        "timeout": 300,
    },
    "amp": {
        "name": "Sourcegraph Amp",
        "search_paths": [],
        "path_env": "AMP_CLI",
        "stdin": True,
        "cmd_template": ["{binary}", "-"],
        "auth_marker_fail": ["not authenticated"],
        "auth_cmd": ["{binary}", "--version"],
        "strengths": ["codebase-search", "exploration", "review"],
        "timeout": 180,
    },
    "gh-copilot": {
        "name": "GitHub Copilot CLI",
        "search_paths": [],
        "path_env": "GH_COPILOT_CLI",
        "stdin": False,
        "cmd_template": ["{binary}", "suggest", "-t", "shell", "{prompt}"],
        "auth_marker_fail": ["not logged in"],
        "auth_cmd": ["{binary}", "--version"],
        "strengths": ["shell", "git", "ci"],
        "timeout": 60,
    },
}

# Custom agents config path
_CUSTOM_AGENTS_PATH = Path.home() / ".devmanager" / "agents.json"

# Cache to avoid re-discovering every call
_discovery_cache: dict[str, dict] | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Discovery
# ─────────────────────────────────────────────────────────────────────────────

def discover_agents(force: bool = False) -> dict[str, dict]:
    """Discover ALL available AI agent CLIs on this machine.

    Sources (in order):
    1. Built-in registry (claude, codex, aider, gemini, …)
    2. Custom ~/.devmanager/agents.json
    3. PATH scan for known binary names
    """
    global _discovery_cache
    if _discovery_cache is not None and not force:
        return _discovery_cache

    found: dict[str, dict] = {}

    # 1. Built-in registry
    for key, spec in _BUILTIN_REGISTRY.items():
        binary = _resolve_binary(key, spec)
        if binary:
            found[key] = {**spec, "binary": str(binary), "source": "builtin"}

    # 2. Custom agents from ~/.devmanager/agents.json
    custom = _load_custom_agents()
    for key, info in custom.items():
        binary_path = Path(info.get("binary", ""))
        if binary_path.exists() and os.access(str(binary_path), os.X_OK):
            found[key] = {**info, "source": "custom"}
        elif shutil.which(info.get("binary", "")):
            found[key] = {**info, "binary": shutil.which(info["binary"]), "source": "custom"}

    # 3. PATH scan — pick up anything we didn't know about
    _PATH_SCAN_NAMES = [
        "aider", "gemini", "opencode", "goose", "amp",
        "cursor", "windsurf", "continue", "cody",
        "tabnine", "supermaven", "phind",
    ]
    for name in _PATH_SCAN_NAMES:
        if name in found:
            continue
        binary = shutil.which(name)
        if binary:
            found[name] = {
                "name": name.capitalize(),
                "binary": binary,
                "stdin": True,
                "cmd_template": [binary, "-"],
                "strengths": ["general"],
                "timeout": 120,
                "source": "path-scan",
                "auth_marker_fail": [],
                "auth_cmd": [binary, "--version"],
            }

    _discovery_cache = found
    return found


def _resolve_binary(key: str, spec: dict) -> Path | None:
    # Check env var override first
    env_val = os.getenv(spec.get("path_env", ""), "")
    if env_val:
        p = Path(env_val)
        if p.exists() and os.access(str(p), os.X_OK):
            return p

    # Check PATH
    which = shutil.which(key)
    if which:
        return Path(which)

    # Check search paths
    for sp in spec.get("search_paths", []):
        if sp == "__CLAUDE_VERSIONED__":
            p = _find_claude_versioned()
            if p:
                return p
            continue
        p = Path(sp)
        if p.exists() and os.access(str(p), os.X_OK):
            return p

    return None


def _find_claude_versioned() -> Path | None:
    base = Path.home() / "Library/Application Support/Claude/claude-code"
    if not base.exists():
        return None
    for version_dir in sorted(base.iterdir(), reverse=True):
        candidate = version_dir / "claude.app/Contents/MacOS/claude"
        if candidate.exists() and os.access(str(candidate), os.X_OK):
            return candidate
    return None


def _load_custom_agents() -> dict[str, dict]:
    try:
        return json.loads(_CUSTOM_AGENTS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def register_custom_agent(
    key: str,
    binary: str,
    name: str = "",
    stdin: bool = True,
    strengths: list[str] | None = None,
) -> None:
    """Persist a new custom agent to ~/.devmanager/agents.json."""
    _CUSTOM_AGENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = _load_custom_agents()
    existing[key] = {
        "name": name or key.capitalize(),
        "binary": binary,
        "stdin": stdin,
        "cmd_template": [binary, "-"] if stdin else [binary, "{prompt}"],
        "strengths": strengths or ["general"],
        "timeout": 180,
        "auth_marker_fail": [],
        "auth_cmd": [binary, "--version"],
    }
    _CUSTOM_AGENTS_PATH.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    global _discovery_cache
    _discovery_cache = None  # invalidate cache


# ─────────────────────────────────────────────────────────────────────────────
# Auth check
# ─────────────────────────────────────────────────────────────────────────────

def check_agent_auth(agent_key: str) -> tuple[bool, str]:
    """Check if agent is authenticated. Returns (ok, message)."""
    agents = discover_agents()
    if agent_key not in agents:
        return False, f"Agent '{agent_key}' not found."

    info = agents[agent_key]
    binary = info["binary"]
    auth_cmd_tmpl = info.get("auth_cmd", [binary, "--version"])
    auth_cmd = [c.replace("{binary}", binary) for c in auth_cmd_tmpl]
    fail_markers = info.get("auth_marker_fail", [])

    try:
        result = subprocess.run(
            auth_cmd,
            capture_output=True, text=True, timeout=15, check=False,
        )
        combined = (result.stdout + result.stderr).lower()
        for marker in fail_markers:
            if marker.lower() in combined:
                return False, (
                    f"{info['name']} not authenticated.\n"
                    f"  Run: {binary} /login  (or open the app and sign in)"
                )
        return True, f"{info['name']} ready."
    except Exception as exc:
        return False, f"Auth check failed: {exc}"


def available_agents() -> dict[str, dict]:
    """Return only agents that are discovered AND authenticated."""
    result = {}
    for key, info in discover_agents().items():
        ok, _ = check_agent_auth(key)
        if ok:
            result[key] = info
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Run agent
# ─────────────────────────────────────────────────────────────────────────────

def run_agent(
    agent_key: str,
    prompt: str,
    repo: str | Path | None = None,
    stream: bool = True,
    timeout: int | None = None,
    extra_cfg: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run any registered agent with the given prompt."""
    agents = discover_agents()
    if agent_key not in agents:
        return {
            "ok": False, "agent": agent_key, "output": "", "returncode": -1,
            "error": f"Agent '{agent_key}' not found. Available: {list(agents.keys()) or ['none']}",
        }

    info = agents[agent_key]
    binary = info["binary"]
    cwd = str(Path(repo).expanduser().resolve()) if repo else str(Path.cwd())
    effective_timeout = timeout or info.get("timeout", 300)

    cmd, stdin_data = _build_command(agent_key, info, prompt, extra_cfg or {})
    env = {**os.environ, "FORCE_COLOR": "0", "NO_COLOR": "1"}

    try:
        if stream:
            return _run_streaming(cmd, stdin_data, cwd, env, effective_timeout, agent_key)
        else:
            return _run_capture(cmd, stdin_data, cwd, env, effective_timeout, agent_key)
    except FileNotFoundError:
        return {
            "ok": False, "agent": agent_key, "output": "", "returncode": -1,
            "error": f"Binary not found: {binary}",
        }


def _build_command(
    agent_key: str,
    info: dict,
    prompt: str,
    extra_cfg: dict,
) -> tuple[list[str], bytes | None]:
    """Build command list and optional stdin bytes for an agent."""
    binary = info["binary"]
    template = info.get("cmd_template", [binary, "{prompt}"])
    use_stdin = info.get("stdin", False)

    # Substitute {binary} and {prompt} in template
    cmd = []
    for part in template:
        part = part.replace("{binary}", binary)
        if not use_stdin:
            part = part.replace("{prompt}", prompt)
        cmd.append(part)

    # Extra config overrides (model, etc.)
    if extra_cfg.get("model") and agent_key == "claude":
        cmd += ["--model", extra_cfg["model"]]
    if extra_cfg.get("model") and agent_key == "codex":
        cmd += ["-c", f'model="{extra_cfg["model"]}"']

    stdin_data = prompt.encode("utf-8") if use_stdin else None
    return cmd, stdin_data


# ─────────────────────────────────────────────────────────────────────────────
# Streaming and capture runners
# ─────────────────────────────────────────────────────────────────────────────

_SUPPRESS_OUTPUT = (
    b"warning: Ignoring malformed",
    b"tokens used",
    b"OpenAI Codex v",
    b"workdir:",
    b"provider:",
    b"approval:",
    b"sandbox:",
    b"reasoning effort:",
    b"reasoning summaries:",
    b"session id:",
    b"--------",
)


def _run_streaming(
    cmd: list[str],
    stdin_data: bytes | None,
    cwd: str,
    env: dict,
    timeout: int,
    agent_key: str,
) -> dict[str, Any]:
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE if stdin_data else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )

    collected_out: list[bytes] = []
    collected_err: list[bytes] = []

    def _drain_stdout(pipe, store: list) -> None:
        buf = b""
        for chunk in iter(lambda: pipe.read(256), b""):
            store.append(chunk)
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                stripped = line.strip()
                if stripped and not any(stripped.startswith(s) for s in _SUPPRESS_OUTPUT):
                    sys.stdout.buffer.write(line + b"\n")
                    sys.stdout.buffer.flush()
        if buf.strip() and not any(buf.strip().startswith(s) for s in _SUPPRESS_OUTPUT):
            sys.stdout.buffer.write(buf)
            sys.stdout.buffer.flush()

    def _drain_stderr(pipe, store: list) -> None:
        for chunk in iter(lambda: pipe.read(256), b""):
            store.append(chunk)

    t_out = threading.Thread(target=_drain_stdout, args=(proc.stdout, collected_out))
    t_err = threading.Thread(target=_drain_stderr, args=(proc.stderr, collected_err))
    t_out.start()
    t_err.start()

    try:
        if stdin_data:
            proc.stdin.write(stdin_data)
            proc.stdin.close()
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        print(f"\n[bridge] {agent_key} timed out after {timeout}s", file=sys.stderr)

    t_out.join()
    t_err.join()

    out = b"".join(collected_out).decode("utf-8", errors="replace").strip()
    err = b"".join(collected_err).decode("utf-8", errors="replace").strip()
    rc = proc.returncode or 0

    return {"ok": rc == 0, "agent": agent_key, "output": out, "returncode": rc,
            "error": err if rc != 0 else None}


def _run_capture(
    cmd: list[str],
    stdin_data: bytes | None,
    cwd: str,
    env: dict,
    timeout: int,
    agent_key: str,
) -> dict[str, Any]:
    try:
        result = subprocess.run(
            cmd, cwd=cwd, env=env, input=stdin_data,
            capture_output=True, timeout=timeout, check=False,
        )
        out = result.stdout.decode("utf-8", errors="replace").strip()
        err = result.stderr.decode("utf-8", errors="replace").strip()
        return {"ok": result.returncode == 0, "agent": agent_key, "output": out,
                "returncode": result.returncode, "error": err if result.returncode != 0 else None}
    except subprocess.TimeoutExpired:
        return {"ok": False, "agent": agent_key, "output": "", "returncode": 124,
                "error": f"Timed out after {timeout}s"}


# ─────────────────────────────────────────────────────────────────────────────
# Smart selection
# ─────────────────────────────────────────────────────────────────────────────

def agents_for_role(role: str) -> list[str]:
    """Return all available+authenticated agents suited for a council role.

    Role → preferred strengths mapping.
    Returns ALL matching agents, not just one.
    """
    role_strengths = {
        "explorer":    ["exploration", "codebase-search", "review", "cross-stack"],
        "analyst":     ["backend", "implementation", "debugging", "refactor"],
        "reviewer":    ["review", "architecture", "cross-stack", "exploration"],
        "implementer": ["implementation", "backend", "refactor", "multi-file"],
        "planner":     ["general", "research"],
        "general":     [],
    }

    preferred = set(role_strengths.get(role, []))
    avail = available_agents()

    # Score each agent by how many preferred strengths it covers
    scored: list[tuple[int, str]] = []
    for key, info in avail.items():
        strengths = set(info.get("strengths", []))
        score = len(strengths & preferred) if preferred else 1
        scored.append((score, key))

    scored.sort(reverse=True)
    return [key for _, key in scored if key != "ollama"]  # ollama handled separately


def agent_for_owner(owner: str) -> str | None:
    """Pick best single authenticated agent for a routing owner."""
    role_map = {"backend": "analyst", "frontend": "explorer", "codex": "reviewer"}
    candidates = agents_for_role(role_map.get(owner, "general"))
    return candidates[0] if candidates else None


# ─────────────────────────────────────────────────────────────────────────────
# Print helpers
# ─────────────────────────────────────────────────────────────────────────────

def print_agents(show_all: bool = False) -> None:
    BOLD = "\033[1m"; DIM = "\033[2m"; GREEN = "\033[32m"
    YELLOW = "\033[33m"; RED = "\033[31m"; CYAN = "\033[36m"; RESET = "\033[0m"

    all_agents = discover_agents(force=True)
    avail = available_agents()

    print(f"\n{BOLD}AI Agents on this machine:{RESET}\n")

    if not all_agents:
        print(f"  {DIM}No agents found. Install Claude Code or Codex to start.{RESET}\n")
        return

    for key, info in all_agents.items():
        if key in avail:
            strengths = ", ".join(info.get("strengths", []))
            print(f"  {GREEN}✓{RESET}  {BOLD}{key:<15}{RESET} {info['name']}")
            print(f"       {DIM}strengths: {strengths}{RESET}")
            print(f"       {DIM}{info['binary']}{RESET}")
        elif show_all:
            ok, msg = check_agent_auth(key)
            print(f"  {RED}✗{RESET}  {DIM}{key:<15}{RESET} {DIM}{info['name']} — {msg.splitlines()[0]}{RESET}")
        print()

    if not show_all and len(all_agents) > len(avail):
        not_ready = len(all_agents) - len(avail)
        print(f"  {DIM}+{not_ready} more discovered but not authenticated. Run 'devm agents --all' to see them.{RESET}\n")

    print(f"  {DIM}devm --agent auto \"task\"           → best agent auto-selected{RESET}")
    print(f"  {DIM}devm --council \"task\"              → all agents collaborate{RESET}")
    print(f"  {DIM}devm agent-add  → add more agents (interactive){RESET}\n")
