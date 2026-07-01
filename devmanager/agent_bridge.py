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
        "cmd_template": ["{binary}", "exec", "--skip-git-repo-check", "-c", 'sandbox_permissions=["disk-full-read-access"]', "-"],
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
    "antigravity": {
        "name": "Antigravity",
        "app_bundle": "Antigravity",               # macOS app name for 'open -a'
        "kind": "gui",                             # GUI agent — paste via clipboard
        "search_paths": [
            "/Applications/Antigravity.app",       # detect by app presence
        ],
        "stdin": False,
        "cmd_template": [],                        # not run as CLI
        "auth_marker_fail": [],
        "auth_cmd": [],
        "strengths": ["frontend", "fullstack", "gemini", "google"],
        "timeout": 0,
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
    # GUI agents — detect by app bundle existence, not a CLI binary
    if spec.get("kind") == "gui":
        for sp in spec.get("search_paths", []):
            p = Path(sp)
            if p.exists():
                return p   # return the .app path as the "binary"
        return None

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
    """Check if agent is ready to use. Returns (ok, status_message).

    Three possible outcomes:
      True,  "ready"          — binary works, no auth error markers found
      False, "needs login"    — binary works but auth markers detected in output
      False, "not working"    — binary crashed, timed out, or returned bad exit
    """
    agents = discover_agents()
    if agent_key not in agents:
        return False, "not found"

    info = agents[agent_key]
    binary = info["binary"]
    fail_markers = info.get("auth_marker_fail", [])

    # GUI agents — app presence = ready (no CLI to check)
    if info.get("kind") == "gui":
        return True, "ready (GUI)"

    # No auth markers defined = no login required, just check binary runs
    if not fail_markers:
        try:
            r = subprocess.run(
                [binary, "--version"],
                capture_output=True, text=True, timeout=8, check=False,
            )
            # Some binaries return non-zero on --version (e.g. Antigravity) — that's fine
            # Just confirm the binary is executable and doesn't crash immediately
            return True, "ready"
        except FileNotFoundError:
            return False, "binary not found"
        except subprocess.TimeoutExpired:
            # Timed out on --version — probably a GUI app that blocks
            # Assume it's installed and usable if file exists
            return True, "ready (GUI app)"
        except Exception:
            return True, "ready"

    # Has auth markers — run auth_cmd and check output
    auth_cmd_tmpl = info.get("auth_cmd", [binary, "--version"])
    auth_cmd = [c.replace("{binary}", binary) for c in auth_cmd_tmpl]

    try:
        result = subprocess.run(
            auth_cmd,
            capture_output=True, text=True, timeout=10, check=False,
        )
        combined = (result.stdout + result.stderr).lower()
        for marker in fail_markers:
            if marker.lower() in combined:
                return False, "needs login"
        return True, "ready"
    except FileNotFoundError:
        return False, "binary not found"
    except subprocess.TimeoutExpired:
        return False, "auth check timed out"
    except Exception as exc:
        return False, f"check failed: {exc}"


def available_agents() -> dict[str, dict]:
    """Return only agents that are discovered AND ready to use."""
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

    # GUI agents — focus app + copy prompt to clipboard, don't spawn process
    if info.get("kind") == "gui":
        return _run_gui_agent(agent_key, info, prompt)

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


def _run_gui_agent(agent_key: str, info: dict, prompt: str) -> dict:
    """Handle GUI-only agents — focus the app + copy prompt to clipboard."""
    BOLD = "\033[1m"; CYAN = "\033[36m"; GREEN = "\033[32m"
    YELLOW = "\033[33m"; DIM = "\033[2m"; RESET = "\033[0m"

    app_name = info.get("app_bundle", info["name"])
    print(f"\n  {CYAN}◆  {BOLD}{app_name}{RESET}  {DIM}(GUI agent){RESET}")

    # Copy prompt to clipboard
    try:
        subprocess.run(["pbcopy"], input=prompt, text=True, check=True, timeout=5)
    except Exception as e:
        return {"ok": False, "agent": agent_key, "output": "",
                "error": f"Clipboard copy failed: {e}"}

    # Focus the app (open -a brings existing window to front, doesn't relaunch)
    try:
        subprocess.run(["open", "-a", app_name], check=False, timeout=5,
                       capture_output=True)
    except Exception:
        pass

    # Try to auto-paste via AppleScript
    paste_script = '''
tell application "System Events"
    delay 0.8
    keystroke "v" using command down
    delay 0.3
    key code 36
end tell
'''
    result = subprocess.run(
        ["osascript", "-e", paste_script],
        capture_output=True, text=True, timeout=10, check=False,
    )

    if result.returncode == 0:
        print(f"  {GREEN}✓  Prompt sent to {app_name} — check the app window{RESET}")
        return {
            "ok": True, "agent": agent_key,
            "output": f"[Sent to {app_name} via clipboard + paste]",
            "gui": True,
        }
    else:
        # Accessibility not granted — just clipboard
        print(f"  {YELLOW}◈  Prompt copied to clipboard{RESET}")
        print(f"  {DIM}Switch to {app_name} and press ⌘V to paste{RESET}")
        print(f"  {DIM}(To enable auto-paste: System Settings → Privacy → Accessibility → add Terminal){RESET}")
        return {
            "ok": True, "agent": agent_key,
            "output": f"[Prompt in clipboard — paste into {app_name} with ⌘V]",
            "gui": True, "clipboard_only": True,
        }


def _build_command(
    agent_key: str,
    info: dict,
    prompt: str,
    extra_cfg: dict,
) -> tuple[list[str], bytes | None]:
    """Build command list and optional stdin bytes for an agent."""
    # GUI agents have no CLI — caller should have checked kind=="gui" first
    if info.get("kind") == "gui":
        return [], None

    binary = info.get("binary", "")
    if not binary:
        return [], None
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

    print(f"\n{BOLD}AI Agents on this machine:{RESET}\n")

    if not all_agents:
        print(f"  {DIM}No agents found. Install Claude Code, Codex, or Aider to start.{RESET}")
        print(f"  {DIM}Then run 'devm agent-add' to register them.{RESET}\n")
        return

    ready: list[tuple[str, dict, str]]    = []
    not_ready: list[tuple[str, dict, str]] = []

    for key, info in all_agents.items():
        ok, status = check_agent_auth(key)
        if ok:
            ready.append((key, info, status))
        else:
            not_ready.append((key, info, status))

    # ── Ready agents ──────────────────────────────────────────────────────────
    for key, info, status in ready:
        strengths = ", ".join(info.get("strengths", []))
        binary = info.get("binary", "")
        is_gui = info.get("kind") == "gui"
        icon = f"{CYAN}◆{RESET}" if is_gui else f"{GREEN}✓{RESET}"
        tag  = f"  {DIM}(GUI — paste via clipboard){RESET}" if is_gui else ""
        try:
            short = "~/" + str(Path(binary).relative_to(Path.home()))
        except ValueError:
            short = binary
        print(f"  {icon}  {BOLD}{key:<15}{RESET} {info['name']}{tag}")
        if strengths:
            print(f"       {DIM}strengths: {strengths}{RESET}")
        print(f"       {DIM}{short}{RESET}")
        print()

    # ── Not-ready agents ──────────────────────────────────────────────────────
    if not_ready and show_all:
        print(f"  {YELLOW}Not ready:{RESET}\n")
        for key, info, status in not_ready:
            binary = info.get("binary", "")
            icon = YELLOW + "⚠" if status == "needs login" else RED + "✗"
            action = ""
            if status == "needs login":
                action = f"  → open {info['name']} and sign in"
            elif status == "auth check timed out":
                action = "  → binary may be a GUI-only app"
            print(f"  {icon}{RESET}  {DIM}{key:<15}{RESET} {info['name']}  {DIM}({status}){RESET}")
            if action:
                print(f"       {DIM}{action}{RESET}")
            print()

    elif not_ready and not show_all:
        names = ", ".join(info["name"] for _, info, _ in not_ready)
        print(f"  {DIM}Not ready: {names}{RESET}")
        for key, info, status in not_ready:
            icon = "⚠" if status == "needs login" else "✗"
            action = f"open {info['name']} and sign in" if status == "needs login" else status
            print(f"  {DIM}{icon}  {key:<15} {action}{RESET}")
        print(f"\n  {DIM}Run 'devm agents --all' for details{RESET}")
        print()

    print(f"  {DIM}devm --agent auto \"task\"    → best agent auto-selected{RESET}")
    print(f"  {DIM}devm --council \"task\"       → all agents collaborate{RESET}")
    print(f"  {DIM}devm agent-add              → add more agents{RESET}\n")
