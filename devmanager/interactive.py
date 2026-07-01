"""Interactive Council UI — Claude Code-style transparent terminal experience.

Shows everything in real-time:
  - Which agent is speaking (with avatar + color)
  - Streaming output character by character
  - Detected actions (commands, file edits) with permission prompts
  - Handoffs between agents with clear context messages
  - User can Allow / Deny / Modify any proposed action

Layout inspiration: Claude Code's tool-use approval flow.
"""
from __future__ import annotations

import os
import re
import sys
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .agent_bridge import run_agent, available_agents, agents_for_role, discover_agents
from .solver import solve as ollama_solve
from .user_config import load as load_cfg


# ─────────────────────────────────────────────────────────────────────────────
# ANSI colours
# ─────────────────────────────────────────────────────────────────────────────

C = {
    "bold":    "\033[1m",
    "dim":     "\033[2m",
    "italic":  "\033[3m",
    "reset":   "\033[0m",
    "cyan":    "\033[36m",
    "green":   "\033[32m",
    "yellow":  "\033[33m",
    "red":     "\033[31m",
    "blue":    "\033[34m",
    "magenta": "\033[35m",
    "white":   "\033[97m",
    "bg_dark": "\033[48;5;235m",
    "orange":  "\033[38;5;208m",
}

# Role → color + emoji
ROLE_STYLE: dict[str, dict] = {
    "planner":     {"color": C["dim"],     "icon": "🧠", "label": "PLANNER"},
    "explorer":    {"color": C["cyan"],    "icon": "🔍", "label": "EXPLORER"},
    "analyst":     {"color": C["green"],   "icon": "⚙️ ", "label": "ANALYST"},
    "reviewer":    {"color": C["yellow"],  "icon": "🔎", "label": "REVIEWER"},
    "contributor": {"color": C["blue"],    "icon": "💡", "label": "CONTRIBUTOR"},
    "synthesizer": {"color": C["magenta"], "icon": "✨", "label": "SYNTHESIZER"},
    "system":      {"color": C["dim"],     "icon": "⬡ ", "label": "SYSTEM"},
    "user":        {"color": C["white"],   "icon": "👤", "label": "YOU"},
    "permission":  {"color": C["orange"],  "icon": "🔐", "label": "PERMISSION"},
}

# Agent → color
AGENT_COLOR: dict[str, str] = {
    "claude":  C["green"],
    "codex":   C["cyan"],
    "ollama":  C["dim"],
    "aider":   C["yellow"],
    "gemini":  C["blue"],
    "default": C["white"],
}


def _agent_color(key: str) -> str:
    return AGENT_COLOR.get(key, AGENT_COLOR["default"])


# ─────────────────────────────────────────────────────────────────────────────
# Action detection — find proposed shell commands / file edits in agent output
# ─────────────────────────────────────────────────────────────────────────────

# Patterns that suggest an agent is proposing a command or file change
_CMD_PATTERNS = [
    re.compile(r"^\s*\$\s+(.+)", re.M),                 # $ npm test
    re.compile(r"```(?:bash|sh|shell)\s*\n(.+?)\n\s*```", re.S),      # ```bash block
    re.compile(r"^run:\s*`(.+?)`", re.M | re.I),        # run: `command`
    re.compile(r"^execute:\s*`(.+?)`", re.M | re.I),
    re.compile(r"`((?:npm|yarn|npx|git|docker|curl|python|node|ts-node|pytest|go|cargo)\s[^`]+)`"),
]

_FILE_EDIT_PATTERNS = [
    re.compile(r"(?:edit|update|modify|change|write to|create)\s+[`'\"]([^`'\"]+\.[a-z]{1,6})[`'\"]", re.I),
    re.compile(r"(?:in|open)\s+[`'\"]([^`'\"]+\.[a-z]{1,6})[`'\"]", re.I),
]

# Commands that are always safe (read-only)
_SAFE_COMMANDS = frozenset([
    "cat", "ls", "find", "grep", "head", "tail", "echo", "pwd",
    "git log", "git status", "git diff", "git show",
    "curl -s", "curl --silent",
    "npm list", "yarn list",
    "docker ps",
])

# Destructive commands — always require confirmation
_DESTRUCTIVE_PATTERNS = re.compile(
    r"\b(rm\s+-rf|drop\s+table|delete\s+from|truncate|format|mkfs|dd\s+if=|git\s+push\s+--force|git\s+reset\s+--hard)\b",
    re.I,
)


@dataclass
class DetectedAction:
    kind: str           # "command" | "file_edit"
    value: str          # the command or file path
    is_destructive: bool = False
    is_safe: bool = False
    line_ctx: str = ""  # surrounding text


def detect_actions(text: str) -> list[DetectedAction]:
    actions: list[DetectedAction] = []
    seen: set[str] = set()

    for pattern in _CMD_PATTERNS:
        for m in pattern.finditer(text):
            cmd = m.group(1).strip()
            if cmd and cmd not in seen:
                seen.add(cmd)
                first_word = cmd.split()[0] if cmd.split() else ""
                is_destructive = bool(_DESTRUCTIVE_PATTERNS.search(cmd))
                is_safe = any(cmd.startswith(s) for s in _SAFE_COMMANDS) and not is_destructive
                actions.append(DetectedAction("command", cmd, is_destructive, is_safe))

    for pattern in _FILE_EDIT_PATTERNS:
        for m in pattern.finditer(text):
            path = m.group(1).strip()
            if path and path not in seen:
                seen.add(path)
                actions.append(DetectedAction("file_edit", path, False, False))

    return actions


# ─────────────────────────────────────────────────────────────────────────────
# Terminal UI helpers
# ─────────────────────────────────────────────────────────────────────────────

def _term_width() -> int:
    try:
        return os.get_terminal_size().columns
    except OSError:
        return 88


def _sep(char: str = "─", color: str = C["dim"]) -> None:
    w = min(_term_width(), 88)
    print(f"{color}{char * w}{C['reset']}")


def _role_header(role: str, agent: str) -> None:
    style = ROLE_STYLE.get(role, ROLE_STYLE["system"])
    acolor = _agent_color(agent)
    icon = style["icon"]
    label = style["label"]
    rcolor = style["color"]
    w = _term_width()
    agent_badge = f"  {acolor}{C['bold']}{agent}{C['reset']}"
    print()
    _sep("─", rcolor)
    print(f"{rcolor}{C['bold']}{icon}  {label}{C['reset']}{agent_badge}")
    _sep("─", rcolor)


def _print_system(msg: str) -> None:
    style = ROLE_STYLE["system"]
    print(f"\n{style['color']}{style['icon']}  {msg}{C['reset']}")


def _print_handoff(from_role: str, to_role: str, summary: str) -> None:
    from_s = ROLE_STYLE.get(from_role, ROLE_STYLE["system"])
    to_s = ROLE_STYLE.get(to_role, ROLE_STYLE["system"])
    print()
    print(
        f"  {from_s['color']}{from_s['icon']} {from_role}{C['reset']}"
        f"  {C['dim']}→{C['reset']}  "
        f"{to_s['color']}{to_s['icon']} {to_role}{C['reset']}"
        f"  {C['dim']}· {summary[:60]}{C['reset']}"
    )


def _elapsed_badge(elapsed: float, ok: bool) -> str:
    icon = f"{C['green']}✓{C['reset']}" if ok else f"{C['red']}✗{C['reset']}"
    return f"{icon}  {C['dim']}{elapsed:.1f}s{C['reset']}"


# ─────────────────────────────────────────────────────────────────────────────
# Spinner — Claude Code style thinking indicator
# ─────────────────────────────────────────────────────────────────────────────

# Braille spinner frames (same as Claude Code)
_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

# Role-specific thinking messages — cycle through these while waiting
_ROLE_MESSAGES: dict[str, list[str]] = {
    "planner": [
        "Thinking…",
        "Breaking down the task…",
        "Planning subtasks…",
        "Still thinking…",
        "Structuring approach…",
    ],
    "explorer": [
        "Reading codebase…",
        "Exploring files…",
        "Scanning repo…",
        "Still exploring…",
        "Tracing code paths…",
        "Finding relevant files…",
        "Inspecting modules…",
    ],
    "analyst": [
        "Analyzing…",
        "Thinking about the fix…",
        "Reading context…",
        "Still analyzing…",
        "Tracing root cause…",
        "Writing solution…",
        "Checking edge cases…",
    ],
    "reviewer": [
        "Reviewing…",
        "Checking the fix…",
        "Looking for issues…",
        "Still reviewing…",
        "Verifying logic…",
        "Checking edge cases…",
        "Cross-referencing…",
    ],
    "contributor": [
        "Adding perspective…",
        "Thinking independently…",
        "Still thinking…",
    ],
    "synthesizer": [
        "Synthesizing…",
        "Merging agent outputs…",
        "Still synthesizing…",
        "Combining findings…",
        "Writing final answer…",
    ],
    "system": ["Working…", "Still working…", "Please wait…"],
}


class Spinner:
    """Animated spinner with rotating status messages, Claude Code style.

    Usage:
        with Spinner("explorer", "codex") as sp:
            # do work — spinner animates in background
            sp.set_message("Found 3 files…")   # optional live update
        # spinner clears itself on exit
    """

    def __init__(self, role: str, agent: str) -> None:
        self.role = role
        self.agent = agent
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._override_msg: str | None = None
        self._first_output = False

    def __enter__(self) -> "Spinner":
        if sys.stdout.isatty():
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        if sys.stdout.isatty():
            # Clear the spinner line
            sys.stdout.write(f"\r\033[K")
            sys.stdout.flush()

    def set_message(self, msg: str) -> None:
        self._override_msg = msg

    def notify_first_output(self) -> None:
        """Call when first real output arrives — spinner clears itself."""
        self._first_output = True
        self._stop.set()
        if sys.stdout.isatty():
            sys.stdout.write(f"\r\033[K")
            sys.stdout.flush()

    def _spin(self) -> None:
        style = ROLE_STYLE.get(self.role, ROLE_STYLE["system"])
        rcolor = style["color"]
        acolor = _agent_color(self.agent)
        messages = _ROLE_MESSAGES.get(self.role, _ROLE_MESSAGES["system"])

        frame_i = 0
        msg_i = 0
        tick = 0

        while not self._stop.is_set():
            frame = _SPINNER_FRAMES[frame_i % len(_SPINNER_FRAMES)]
            msg = self._override_msg or messages[msg_i % len(messages)]
            elapsed = tick * 0.08

            # Every ~4s rotate to next message
            if tick > 0 and tick % 50 == 0:
                msg_i += 1
                self._override_msg = None  # clear override after one cycle

            # Format: ⠋  claude  Analyzing…  (12.3s)
            elapsed_str = f"{C['dim']}({elapsed:.0f}s){C['reset']}" if elapsed > 3 else ""
            line = (
                f"\r  {rcolor}{frame}{C['reset']}  "
                f"{acolor}{C['bold']}{self.agent}{C['reset']}  "
                f"{C['dim']}{msg}{C['reset']}  "
                f"{elapsed_str}"
            )
            sys.stdout.write(line)
            sys.stdout.flush()

            time.sleep(0.08)
            frame_i += 1
            tick += 1


# ─────────────────────────────────────────────────────────────────────────────
# Permission prompt — Claude Code style
# ─────────────────────────────────────────────────────────────────────────────

class PermissionDenied(Exception):
    pass


def ask_permission(action: DetectedAction, agent: str) -> str:
    """Show permission prompt and return 'allow' | 'skip' | 'modify:<new>'."""
    if not sys.stdin.isatty():
        return "allow"  # Non-interactive mode: auto-allow

    style = ROLE_STYLE["permission"]
    acolor = _agent_color(agent)
    w = min(_term_width(), 88)

    print()
    print(f"  {style['color']}{C['bold']}╭─ {style['icon']}  Permission Request {'─' * (w - 30)}╮{C['reset']}")

    kind_label = "Run command" if action.kind == "command" else "Edit file"
    danger = f"  {C['red']}{C['bold']}⚠  DESTRUCTIVE{C['reset']}" if action.is_destructive else ""
    safe = f"  {C['green']}(read-only){C['reset']}" if action.is_safe else ""

    print(f"  {style['color']}│{C['reset']}  {acolor}{C['bold']}{agent}{C['reset']} wants to: {C['bold']}{kind_label}{C['reset']}{danger}{safe}")
    print(f"  {style['color']}│{C['reset']}")
    print(f"  {style['color']}│{C['reset']}  {C['bold']}{C['bg_dark']}  {action.value}  {C['reset']}")
    print(f"  {style['color']}│{C['reset']}")

    if action.is_destructive:
        print(f"  {style['color']}│{C['reset']}  {C['red']}This command is potentially destructive. Be careful.{C['reset']}")

    print(f"  {style['color']}╰{'─' * (w - 4)}╯{C['reset']}")
    print()

    # Options
    if action.is_safe:
        default_hint = f"[{C['green']}A{C['reset']}]llow  [{C['dim']}S{C['reset']}]kip"
    elif action.is_destructive:
        default_hint = f"[{C['red']}A{C['reset']}]llow  [{C['yellow']}S{C['reset']}]kip  [{C['cyan']}M{C['reset']}]odify"
    else:
        default_hint = f"[{C['green']}A{C['reset']}]llow  [{C['yellow']}S{C['reset']}]kip  [{C['cyan']}M{C['reset']}]odify"

    try:
        answer = input(f"  {default_hint}  > ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return "skip"

    if answer in ("a", "allow", "y", "yes", ""):
        return "allow"
    if answer in ("s", "skip", "n", "no"):
        return "skip"
    if answer.startswith("m") or answer.startswith("modify"):
        try:
            new_cmd = input(f"  {C['cyan']}Modified command{C['reset']} > ").strip()
        except (EOFError, KeyboardInterrupt):
            return "skip"
        return f"modify:{new_cmd}"

    return "skip"


def run_approved_command(cmd: str, repo: str) -> str:
    """Run a shell command that the user approved and stream its output."""
    print(f"\n  {C['dim']}$ {cmd}{C['reset']}")
    _sep("·", C["dim"])
    try:
        proc = subprocess.Popen(
            cmd, shell=True, cwd=repo,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        output_lines: list[str] = []
        for line in proc.stdout:  # type: ignore[union-attr]
            print(f"  {C['dim']}{line.rstrip()}{C['reset']}")
            output_lines.append(line)
        proc.wait()
        status = f"{C['green']}✓ exit 0{C['reset']}" if proc.returncode == 0 else f"{C['red']}✗ exit {proc.returncode}{C['reset']}"
        print(f"\n  {status}")
        return "".join(output_lines)
    except Exception as exc:
        print(f"  {C['red']}Error: {exc}{C['reset']}")
        return str(exc)


# ─────────────────────────────────────────────────────────────────────────────
# Live streaming wrapper — streams agent output and handles permission checks
# ─────────────────────────────────────────────────────────────────────────────

_SUPPRESS = (
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


def run_agent_interactive(
    agent_key: str,
    prompt: str,
    role: str,
    repo: str,
    interactive: bool = True,
    timeout: int = 300,
) -> dict[str, Any]:
    """Run an agent with live streaming + optional permission prompts."""
    from .agent_bridge import discover_agents, _build_command  # type: ignore[attr-defined]

    agents = discover_agents()
    if agent_key not in agents:
        return {"ok": False, "output": "", "error": f"Agent '{agent_key}' not found"}

    info = agents[agent_key]
    binary = info["binary"]
    cwd = str(Path(repo).expanduser().resolve()) if repo else str(Path.cwd())
    cmd, stdin_data = _build_command(agent_key, info, prompt, {})
    env = {**os.environ, "FORCE_COLOR": "0", "NO_COLOR": "1"}

    _role_header(role, agent_key)
    collected_lines: list[str] = []
    collected_err: list[bytes] = []
    first_output_shown = False

    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE if stdin_data else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )

    def _drain_stderr(pipe: Any) -> None:
        for chunk in iter(lambda: pipe.read(256), b""):
            collected_err.append(chunk)

    t_err = threading.Thread(target=_drain_stderr, args=(proc.stderr,))
    t_err.start()

    spinner = Spinner(role, agent_key)
    spinner.__enter__()

    try:
        if stdin_data:
            proc.stdin.write(stdin_data)  # type: ignore[union-attr]
            proc.stdin.close()            # type: ignore[union-attr]

        buf = b""
        for chunk in iter(lambda: proc.stdout.read(64), b""):  # type: ignore[union-attr]
            buf += chunk
            while b"\n" in buf:
                line_b, buf = buf.split(b"\n", 1)
                stripped = line_b.strip()
                if stripped and not any(stripped.startswith(s) for s in _SUPPRESS):
                    if not first_output_shown:
                        spinner.notify_first_output()
                        first_output_shown = True
                    line = line_b.decode("utf-8", errors="replace")
                    print(f"  {line}")
                    collected_lines.append(line)
        if buf.strip() and not any(buf.strip().startswith(s) for s in _SUPPRESS):
            if not first_output_shown:
                spinner.notify_first_output()
            line = buf.decode("utf-8", errors="replace")
            print(f"  {line}")
            collected_lines.append(line)

        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        spinner.__exit__(None, None, None)
        print(f"\n  {C['red']}⏱  {agent_key} timed out after {timeout}s{C['reset']}")
    finally:
        spinner.__exit__(None, None, None)
        t_err.join()

    output = "\n".join(collected_lines).strip()
    rc = proc.returncode or 0

    # ── Permission check: scan output for proposed actions ────────────────
    if interactive and sys.stdin.isatty():
        actions = detect_actions(output)
        non_safe = [a for a in actions if not a.is_safe and a.kind == "command"]
        if non_safe:
            print()
            _sep("·", C["yellow"])
            print(f"  {C['yellow']}{C['bold']}{len(non_safe)} proposed action(s) detected in {agent_key}'s output:{C['reset']}")
            cmd_results: list[str] = []
            for action in non_safe:
                decision = ask_permission(action, agent_key)
                if decision == "allow":
                    result_text = run_approved_command(action.value, cwd)
                    cmd_results.append(f"[ran: {action.value}]\n{result_text}")
                elif decision.startswith("modify:"):
                    new_cmd = decision[len("modify:"):]
                    result_text = run_approved_command(new_cmd, cwd)
                    cmd_results.append(f"[ran modified: {new_cmd}]\n{result_text}")
                else:
                    print(f"  {C['dim']}⊘ Skipped: {action.value}{C['reset']}")
            if cmd_results:
                output += "\n\nCommand results:\n" + "\n".join(cmd_results)

    return {
        "ok": rc == 0,
        "output": output,
        "returncode": rc,
        "error": b"".join(collected_err).decode("utf-8", errors="replace") if rc != 0 else None,
    }


def run_ollama_interactive(
    prompt: str,
    role: str,
    cfg: dict[str, Any],
    label: str = "ollama",
) -> str:
    """Stream Ollama output live with role header + spinner until first token."""
    import json
    import urllib.request

    _role_header(role, label)

    base_url = cfg.get("base_url", "http://127.0.0.1:11434")
    model = cfg.get("model", "glm4:latest")
    payload = json.dumps({"model": model, "prompt": prompt, "stream": True}).encode()

    spinner = Spinner(role, label)
    spinner.__enter__()
    first_token = False

    try:
        req = urllib.request.Request(
            f"{base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        output_parts: list[str] = []
        with urllib.request.urlopen(req, timeout=120) as resp:
            for raw_line in resp:
                try:
                    obj = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                token = obj.get("response", "")
                if token:
                    if not first_token:
                        spinner.notify_first_output()
                        first_token = True
                        sys.stdout.write("  ")
                        sys.stdout.flush()
                    display = token.replace("\n", "\n  ")
                    sys.stdout.write(display)
                    sys.stdout.flush()
                    output_parts.append(token)
                if obj.get("done"):
                    break
        print()
        return "".join(output_parts).strip()
    except Exception as exc:
        spinner.__exit__(None, None, None)
        print(f"\n  {C['red']}Ollama error: {exc}{C['reset']}")
        return ""
    finally:
        spinner.__exit__(None, None, None)


# ─────────────────────────────────────────────────────────────────────────────
# InteractiveCouncil — the main class
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AgentTurn:
    role: str
    agent: str
    output: str
    elapsed: float
    ok: bool


@dataclass
class InteractiveSession:
    task: str
    repo: str
    turns: list[AgentTurn] = field(default_factory=list)
    final: str = ""
    total_elapsed: float = 0.0


class InteractiveCouncil:
    """Multi-agent council with Claude Code-style live terminal UI."""

    def __init__(
        self,
        task: str,
        repo: str,
        handoff_prompt: str,
        cfg: dict[str, Any] | None = None,
        interactive: bool = True,
    ) -> None:
        self.task = task
        self.repo = repo
        self.handoff = handoff_prompt
        self.cfg = cfg or load_cfg()
        self.interactive = interactive and sys.stdin.isatty()
        self.avail = available_agents()
        self.session = InteractiveSession(task=task, repo=repo)

    # ── Public ───────────────────────────────────────────────────────────

    def run(self) -> InteractiveSession:
        t0 = time.time()
        self._print_session_header()

        # 1. Plan
        subtasks = self._plan()

        # 2. Explore — all exploration-suited agents
        explorer_out = self._explore(subtasks)

        # 3. Analyse — all impl-suited agents
        analyst_out = self._analyse(subtasks, explorer_out)

        # 4. Review — independent agents
        reviewer_out = self._review(analyst_out, explorer_out)

        # 5. Extra agents (any not yet used)
        self._extra(subtasks, analyst_out, explorer_out)

        # 6. Synthesize
        final = self._synthesize()

        self.session.final = final
        self.session.total_elapsed = time.time() - t0
        self._print_session_footer()
        return self.session

    # ── Steps ─────────────────────────────────────────────────────────────

    def _plan(self) -> str:
        prompt = (
            f"You are a dev task planner. Break this task into 2-3 concrete subtasks.\n"
            f"Be specific. Output ONLY a numbered list.\n\n"
            f"Task: {self.task}\n\nRepo context:\n{self.handoff[:800]}"
        )
        t0 = time.time()
        output = run_ollama_interactive(prompt, "planner", self.cfg)
        elapsed = time.time() - t0
        print(f"\n  {_elapsed_badge(elapsed, bool(output))}")
        if not output:
            output = f"1. Investigate {self.task}\n2. Find root cause\n3. Apply fix"
        self.session.turns.append(AgentTurn("planner", "ollama", output, elapsed, True))
        return output

    def _explore(self, subtasks: str) -> str:
        explorers = agents_for_role("explorer")
        if not explorers:
            explorers = list(self.avail.keys())[:1]

        combined: list[str] = []
        for i, key in enumerate(explorers):
            if i > 0:
                _print_handoff("planner", "explorer", f"Now {key} also explores…")
            prompt = (
                f"You are a code explorer. READ ONLY — do NOT write code or fixes.\n\n"
                f"Task: {self.task}\n\nSubtasks:\n{subtasks}\n\n"
                f"Repo context:\n{self.handoff}\n\n"
                f"Report:\n1. Relevant files (exact paths)\n"
                f"2. Key functions/classes involved\n"
                f"3. Root cause hypothesis\n"
                f"4. Any contracts between modules"
            )
            _print_handoff("planner", "explorer", f"Subtasks ready → explore repo") if i == 0 else None
            t0 = time.time()
            result = run_agent_interactive(key, prompt, "explorer", self.repo, self.interactive)
            elapsed = time.time() - t0
            print(f"\n  {_elapsed_badge(elapsed, result['ok'])}")
            if result["output"]:
                combined.append(f"=== {key} exploration ===\n{result['output']}")
            self.session.turns.append(AgentTurn("explorer", key, result["output"], elapsed, result["ok"]))

        if not combined:
            # Ollama fallback
            prompt = f"Explore this task and find relevant code:\nTask: {self.task}\n\nContext:\n{self.handoff}"
            t0 = time.time()
            out = run_ollama_interactive(prompt, "explorer", self.cfg)
            elapsed = time.time() - t0
            print(f"\n  {_elapsed_badge(elapsed, bool(out))}")
            self.session.turns.append(AgentTurn("explorer", "ollama", out, elapsed, True))
            return out

        return "\n\n".join(combined)

    def _analyse(self, subtasks: str, explorer_out: str) -> str:
        analysts = agents_for_role("analyst")
        if not analysts:
            analysts = list(self.avail.keys())[:1]

        combined: list[str] = []
        for i, key in enumerate(analysts):
            _print_handoff("explorer", "analyst", "Explorer findings ready → write fix")
            prompt = (
                f"You are an expert software engineer. The explorer found the relevant code.\n"
                f"Write a concrete fix.\n\n"
                f"Task: {self.task}\n\nSubtasks:\n{subtasks}\n\n"
                f"Explorer findings:\n{explorer_out}\n\n"
                f"Repo context:\n{self.handoff[:2000]}\n\n"
                f"Output:\n"
                f"1. Root cause (1-2 sentences per subtask)\n"
                f"2. Exact code changes (show diff or before/after)\n"
                f"3. Files to touch\n"
                f"4. Risks / side effects\n"
                f"5. Verification commands"
            )
            t0 = time.time()
            result = run_agent_interactive(key, prompt, "analyst", self.repo, self.interactive)
            elapsed = time.time() - t0
            print(f"\n  {_elapsed_badge(elapsed, result['ok'])}")
            if result["output"]:
                combined.append(f"=== {key} analysis ===\n{result['output']}")
            self.session.turns.append(AgentTurn("analyst", key, result["output"], elapsed, result["ok"]))

        if not combined:
            prompt = f"Analyse and fix this task:\n{self.task}\n\nExplorer:\n{explorer_out}\n\nContext:\n{self.handoff[:2000]}"
            t0 = time.time()
            out = run_ollama_interactive(prompt, "analyst", self.cfg)
            elapsed = time.time() - t0
            print(f"\n  {_elapsed_badge(elapsed, bool(out))}")
            self.session.turns.append(AgentTurn("analyst", "ollama", out, elapsed, True))
            return out

        return "\n\n".join(combined)

    def _review(self, analyst_out: str, explorer_out: str) -> str:
        analyst_agents = {t.agent for t in self.session.turns if t.role == "analyst"}
        reviewers = agents_for_role("reviewer")
        independent = [a for a in reviewers if a not in analyst_agents]
        pool = independent if independent else reviewers
        if not pool:
            pool = list(self.avail.keys())

        combined: list[str] = []
        for key in pool:
            _print_handoff("analyst", "reviewer", "Fix proposed → independent review")
            prompt = (
                f"You are a strict code reviewer. Another agent proposed a fix. Find problems.\n\n"
                f"Task: {self.task}\n\n"
                f"Explorer findings:\n{explorer_out[:1500]}\n\n"
                f"Proposed fix:\n{analyst_out}\n\n"
                f"Review:\n"
                f"- Is the root cause correct?\n"
                f"- Edge cases missed?\n"
                f"- Will this break existing tests?\n"
                f"- Security implications?\n"
                f"- Is the fix minimal?\n\n"
                f"Output:\n1. VERDICT: APPROVE / REQUEST_CHANGES / NEEDS_DISCUSSION\n"
                f"2. Issues found\n3. Suggested improvements\n4. Final recommendation"
            )
            t0 = time.time()
            result = run_agent_interactive(key, prompt, "reviewer", self.repo, self.interactive)
            elapsed = time.time() - t0
            print(f"\n  {_elapsed_badge(elapsed, result['ok'])}")
            if result["output"]:
                combined.append(f"=== {key} review ===\n{result['output']}")
            self.session.turns.append(AgentTurn("reviewer", key, result["output"], elapsed, result["ok"]))

        if not combined:
            prompt = f"Review this fix:\n{analyst_out}\n\nFor task: {self.task}"
            t0 = time.time()
            out = run_ollama_interactive(prompt, "reviewer", self.cfg)
            elapsed = time.time() - t0
            print(f"\n  {_elapsed_badge(elapsed, bool(out))}")
            self.session.turns.append(AgentTurn("reviewer", "ollama", out, elapsed, True))
            return out

        return "\n\n".join(combined)

    def _extra(self, subtasks: str, analyst_out: str, explorer_out: str) -> None:
        used = {t.agent for t in self.session.turns}
        remaining = [k for k in self.avail if k not in used]
        for key in remaining:
            _print_handoff("reviewer", "contributor", f"{key} adds independent perspective")
            prompt = (
                f"Task: {self.task}\n\n"
                f"Other agents have explored and proposed a fix. "
                f"Add your independent perspective.\n\n"
                f"Explorer:\n{explorer_out[:800]}\n\n"
                f"Proposed fix:\n{analyst_out[:1200]}\n\n"
                f"What did others miss? Alternative approach? Additional risks?"
            )
            t0 = time.time()
            result = run_agent_interactive(key, prompt, "contributor", self.repo, self.interactive)
            elapsed = time.time() - t0
            print(f"\n  {_elapsed_badge(elapsed, result['ok'])}")
            if result["output"]:
                self.session.turns.append(AgentTurn("contributor", key, result["output"], elapsed, result["ok"]))

    def _synthesize(self) -> str:
        _print_handoff("reviewer", "synthesizer", "All agent outputs → merge into final answer")
        transcript = "\n\n".join(
            f"=== {t.role.upper()} ({t.agent}) ===\n{t.output}"
            for t in self.session.turns if t.output
        )
        prompt = (
            f"You are a synthesis agent. Multiple AI agents worked on this task.\n"
            f"Combine into ONE clear final answer.\n\n"
            f"Task: {self.task}\n\n"
            f"Agent outputs:\n{transcript}\n\n"
            f"Final answer:\n"
            f"1. Root cause\n2. Exact fix (code + files)\n"
            f"3. Verification steps\n4. Open questions / risks\n\n"
            f"Be concise. Prioritize analyst code + reviewer corrections."
        )
        t0 = time.time()
        output = run_ollama_interactive(prompt, "synthesizer", self.cfg)
        elapsed = time.time() - t0
        print(f"\n  {_elapsed_badge(elapsed, bool(output))}")
        if output:
            self.session.turns.append(AgentTurn("synthesizer", "ollama", output, elapsed, True))
        return output

    # ── Header / Footer ───────────────────────────────────────────────────

    def _print_session_header(self) -> None:
        w = min(_term_width(), 88)
        agent_names = list(self.avail.keys())
        agents_str = "  ".join(
            f"{_agent_color(k)}{C['bold']}{k}{C['reset']}" for k in agent_names
        ) or f"{C['dim']}ollama only{C['reset']}"

        print()
        print(f"{C['bold']}╭{'─' * (w - 2)}╮{C['reset']}")
        print(f"{C['bold']}│{C['reset']}  {C['bold']}DevManager Council{C['reset']}  {C['dim']}—  transparent · interactive{C['reset']}{' ' * max(0, w - 50)}  {C['bold']}│{C['reset']}")
        print(f"{C['bold']}│{C['reset']}")
        task_display = self.task[:w - 14]
        print(f"{C['bold']}│{C['reset']}  {C['bold']}Task:{C['reset']}   {task_display}")
        print(f"{C['bold']}│{C['reset']}  {C['bold']}Agents:{C['reset']}  {agents_str}")
        print(f"{C['bold']}│{C['reset']}  {C['bold']}Repo:{C['reset']}   {C['dim']}{self.repo}{C['reset']}")
        print(f"{C['bold']}│{C['reset']}")
        print(f"{C['bold']}│{C['reset']}  {C['dim']}Pipeline:  Planner → Explorer → Analyst → Reviewer → Synthesizer{C['reset']}")
        if self.interactive:
            print(f"{C['bold']}│{C['reset']}  {C['dim']}Mode:      interactive · press Ctrl+C to abort{C['reset']}")
        print(f"{C['bold']}╰{'─' * (w - 2)}╯{C['reset']}")

    def _print_session_footer(self) -> None:
        w = min(_term_width(), 88)
        t = self.session.total_elapsed
        n = len(self.session.turns)
        agents_used = list({t.agent for t in self.session.turns})
        print()
        _sep("═", C["bold"])
        print(f"{C['bold']}✓  Council complete{C['reset']}  {C['dim']}{t:.1f}s · {n} turns · agents: {', '.join(agents_used)}{C['reset']}")
        _sep("═", C["bold"])

        # Ask user if they want to save the transcript
        if self.interactive:
            try:
                ans = input(f"\n  {C['dim']}Save transcript? [y/N]{C['reset']} > ").strip().lower()
                if ans in ("y", "yes"):
                    out_path = Path(self.repo) / ".devmanager_council.md"
                    out_path.write_text(self._transcript(), encoding="utf-8")
                    print(f"  {C['green']}✓ Saved:{C['reset']} {out_path}")
            except (EOFError, KeyboardInterrupt):
                pass
        print()

    def _transcript(self) -> str:
        parts = [f"# DevManager Council\n\nTask: {self.task}\n"]
        for turn in self.session.turns:
            parts.append(f"\n## {turn.role.upper()} via {turn.agent} ({turn.elapsed:.1f}s)")
            parts.append(turn.output or "(no output)")
        parts.append(f"\n## FINAL ANSWER\n{self.session.final}")
        return "\n".join(parts)
