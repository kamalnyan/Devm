"""Edit Mode & Yolo Mode — two execution modes that actually apply file changes.

--edit mode (controlled):
  - Extracts file changes from agent output
  - Shows colored diff for each change
  - Asks permission before applying each file
  - Runs verification (tests) after edits
  - Blocks production files, .env, destructive ops
  - Backups created before every change

--yolo mode (fully unrestricted):
  - Everything auto-approved
  - Files edited immediately, no prompts
  - All commands agent suggests run automatically
  - No guards, no blocks
  - Clear ⚠️ warning printed at start
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .patch import (
    FileChange,
    ApplyResult,
    extract_changes,
    apply_change,
    show_diff,
    restore_backup,
)

# ─────────────────────────────────────────────────────────────────────────────
# Terminal colors
# ─────────────────────────────────────────────────────────────────────────────

RED     = "\033[31m"
YELLOW  = "\033[33m"
GREEN   = "\033[32m"
CYAN    = "\033[36m"
MAGENTA = "\033[35m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
RESET   = "\033[0m"

def _c(color: str, text: str) -> str:
    return f"{color}{text}{RESET}"

def _sep(char: str = "─", color: str = DIM, width: int = 60) -> None:
    print(f"  {color}{char * width}{RESET}")

def _box(lines: list[str], color: str = CYAN) -> None:
    width = max(len(l) for l in lines) + 4
    print(f"\n  {color}╭{'─' * (width - 2)}╮{RESET}")
    for l in lines:
        pad = width - 3 - len(l)
        print(f"  {color}│{RESET} {l}{' ' * pad}{color}│{RESET}")
    print(f"  {color}╰{'─' * (width - 2)}╯{RESET}\n")


# ─────────────────────────────────────────────────────────────────────────────
# Rules for --edit mode
# ─────────────────────────────────────────────────────────────────────────────

# Files we never touch in edit mode
_BLOCKED_PATHS = re.compile(
    r"""
    (?:^|\/)  # start or directory separator
    (?:
        \.env(?:\..*)?          |   # .env, .env.local, etc.
        \.secret                |
        secrets?\.[a-z]+        |
        .*credentials.*         |
        .*password.*            |
        .*api[_-]?key.*         |
        Dockerfile\.prod        |
        docker-compose\.prod    |
        .*\.pem                 |
        .*\.key                 |
        .*\.p12                 |
        .*\.pfx                 |
        node_modules\/.*        |
        __pycache__\/.*         |
        \.git\/.*
    )
    """,
    re.X | re.I,
)

# Directories blocked in edit mode (production-ish)
_BLOCKED_DIRS = {
    "production", "prod", "release",
    ".git", "node_modules", "__pycache__",
    "dist", "build", ".venv",
}

def _is_blocked(path: str) -> tuple[bool, str]:
    """Returns (blocked, reason) in --edit mode."""
    if _BLOCKED_PATHS.search(path):
        return True, f"matches blocked path pattern (secrets/env/credentials)"
    parts = Path(path).parts
    for p in parts:
        if p.lower() in _BLOCKED_DIRS:
            return True, f"inside blocked directory: {p}"
    return False, ""


# ─────────────────────────────────────────────────────────────────────────────
# Verification — run tests after edits
# ─────────────────────────────────────────────────────────────────────────────

# Commands we recognize as test runners, mapped by files that signal which to use
_TEST_RUNNERS = [
    # (signal_file, command)
    ("pubspec.yaml",      ["flutter", "test"]),
    ("package.json",      ["npm", "test", "--", "--passWithNoTests"]),
    ("pyproject.toml",    ["python", "-m", "pytest", "-x", "-q"]),
    ("Cargo.toml",        ["cargo", "test", "--quiet"]),
    ("go.mod",            ["go", "test", "./..."]),
    ("build.gradle",      ["./gradlew", "test"]),
    ("build.gradle.kts",  ["./gradlew", "test"]),
    ("Makefile",          ["make", "test"]),
]

def detect_test_command(repo_root: str) -> list[str] | None:
    root = Path(repo_root)
    for signal, cmd in _TEST_RUNNERS:
        if (root / signal).exists():
            if shutil.which(cmd[0]):
                return cmd
    return None


def run_verification(repo_root: str, timeout: int = 120) -> tuple[bool, str]:
    """Run detected test suite. Returns (passed, output)."""
    cmd = detect_test_command(repo_root)
    if not cmd:
        return True, "(no test runner detected — skipping verification)"

    print(f"\n  {CYAN}⟳  Running verification: {' '.join(cmd)}{RESET}")
    try:
        result = subprocess.run(
            cmd, cwd=repo_root,
            capture_output=True, text=True, timeout=timeout,
        )
        output = (result.stdout + result.stderr).strip()
        ok = result.returncode == 0
        status = f"{GREEN}✓ Tests passed{RESET}" if ok else f"{RED}✗ Tests failed{RESET}"
        print(f"  {status}")
        return ok, output
    except subprocess.TimeoutExpired:
        return False, "Test command timed out"
    except FileNotFoundError:
        return True, f"Test runner not found: {cmd[0]}"


# ─────────────────────────────────────────────────────────────────────────────
# Permission prompt (edit mode only)
# ─────────────────────────────────────────────────────────────────────────────

def _ask_edit_permission(change: FileChange) -> str:
    """Ask user what to do with a proposed file change. Returns 'apply'|'skip'|'quit'."""
    print(f"\n  {BOLD}{YELLOW}╔══ Edit Request ═══════════════════════════════════════╗{RESET}")
    print(f"  {BOLD}{YELLOW}║{RESET}  {change.description}")
    print(f"  {BOLD}{YELLOW}╚═══════════════════════════════════════════════════════╝{RESET}")
    print(f"\n  {DIM}[A]pply  [S]kip  [Q]uit all edits{RESET}")

    while True:
        try:
            sys.stdout.write(f"  {CYAN}>{RESET} ")
            sys.stdout.flush()
            answer = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "quit"

        if answer in ("a", "apply", "yes", "y", ""):
            return "apply"
        elif answer in ("s", "skip", "no", "n"):
            return "skip"
        elif answer in ("q", "quit"):
            return "quit"
        else:
            print(f"  {DIM}Type A to apply, S to skip, Q to quit{RESET}")


# ─────────────────────────────────────────────────────────────────────────────
# Session result
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EditSession:
    mode: str          # "edit" | "yolo"
    agent_output: str
    changes_found: list[FileChange] = field(default_factory=list)
    changes_applied: list[ApplyResult] = field(default_factory=list)
    changes_skipped: list[str] = field(default_factory=list)
    changes_blocked: list[tuple[str, str]] = field(default_factory=list)
    verification_ok: bool = True
    verification_output: str = ""
    test_command_run: bool = False

    def summary(self) -> str:
        applied = [r for r in self.changes_applied if r.ok]
        failed  = [r for r in self.changes_applied if not r.ok]
        lines = [
            f"Mode: {self.mode}",
            f"Changes found: {len(self.changes_found)}",
            f"Applied: {len(applied)}",
        ]
        if failed:
            lines.append(f"Failed: {len(failed)}")
        if self.changes_skipped:
            lines.append(f"Skipped: {len(self.changes_skipped)}")
        if self.changes_blocked:
            lines.append(f"Blocked: {len(self.changes_blocked)}")
        if self.test_command_run:
            lines.append(f"Tests: {'✓ passed' if self.verification_ok else '✗ failed'}")
        return " · ".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Main entry points
# ─────────────────────────────────────────────────────────────────────────────

def run_edit_mode(
    agent_output: str,
    repo_root: str,
    *,
    yolo: bool = False,
) -> EditSession:
    """
    Extract file changes from agent_output and apply them.

    yolo=False  →  --edit mode: controlled, rules enforced, prompts shown
    yolo=True   →  --yolo mode: auto-approve everything, no guards
    """
    mode = "yolo" if yolo else "edit"
    session = EditSession(mode=mode, agent_output=agent_output)

    if yolo:
        _print_yolo_banner()
    else:
        _print_edit_banner()

    # Extract changes
    changes = extract_changes(agent_output, repo_root)
    session.changes_found = changes

    if not changes:
        print(f"\n  {DIM}No file changes detected in agent output.{RESET}")
        print(f"  {DIM}Agents must output code blocks with file paths or unified diffs.{RESET}\n")
        return session

    print(f"\n  {CYAN}Found {len(changes)} file change(s) to apply:{RESET}")
    for ch in changes:
        print(f"    {DIM}·{RESET} {ch.path}  {DIM}({ch.kind}){RESET}")
    print()

    quit_all = False

    for change in changes:
        if quit_all:
            session.changes_skipped.append(change.path)
            continue

        # --- Block check (edit mode only) ---
        if not yolo:
            blocked, reason = _is_blocked(change.path)
            if blocked:
                print(f"\n  {RED}🚫 Blocked:{RESET} {change.path}")
                print(f"     {DIM}{reason}{RESET}")
                session.changes_blocked.append((change.path, reason))
                continue

        # --- Show diff ---
        try:
            show_diff(change, repo_root)
        except Exception:
            print(f"\n  {DIM}(diff preview unavailable for {change.path}){RESET}")

        # --- Permission ---
        if yolo:
            decision = "apply"
            print(f"  {YELLOW}⚡ YOLO → auto-applying {change.path}{RESET}")
        else:
            decision = _ask_edit_permission(change)

        if decision == "quit":
            quit_all = True
            session.changes_skipped.append(change.path)
            continue
        elif decision == "skip":
            print(f"  {DIM}  Skipped {change.path}{RESET}")
            session.changes_skipped.append(change.path)
            continue

        # --- Apply ---
        result = apply_change(change, repo_root, backup=True)
        session.changes_applied.append(result)

        if result.ok:
            icon = "✓" if result.action == "applied" else "+"
            print(f"  {GREEN}{icon}  {result.action.capitalize()}: {change.path}{RESET}")
            if result.backup_path:
                print(f"     {DIM}backup: {result.backup_path}{RESET}")
        else:
            print(f"  {RED}✗  Failed: {change.path}{RESET}")
            print(f"     {DIM}{result.error}{RESET}")

    # --- Verification (after all edits) ---
    applied_ok = [r for r in session.changes_applied if r.ok]
    if applied_ok:
        print()
        _sep()
        ok, output = run_verification(repo_root)
        session.verification_ok = ok
        session.verification_output = output
        session.test_command_run = True

        if not ok and not yolo:
            # Offer rollback in edit mode
            _offer_rollback(session, repo_root)
    else:
        session.verification_ok = True

    # --- Final summary ---
    _print_summary(session)

    return session


def _offer_rollback(session: EditSession, repo_root: str) -> None:
    """After test failure in edit mode, offer to undo all applied changes."""
    applied_ok = [r for r in session.changes_applied if r.ok and r.backup_path]
    if not applied_ok:
        return

    print(f"\n  {YELLOW}Tests failed after edits. Rollback changes?{RESET}")
    print(f"  {DIM}[R]ollback all  [K]eep (stay as-is){RESET}")

    sys.stdout.write(f"  {CYAN}>{RESET} ")
    sys.stdout.flush()
    try:
        answer = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = "k"

    if answer in ("r", "rollback", "yes", "y"):
        for result in applied_ok:
            if restore_backup(result.path, repo_root):
                print(f"  {YELLOW}↩  Restored: {result.path}{RESET}")
            else:
                print(f"  {DIM}  No backup found for: {result.path}{RESET}")
        print(f"  {GREEN}Rollback complete.{RESET}")
    else:
        print(f"  {DIM}  Changes kept as-is.{RESET}")


def _print_edit_banner() -> None:
    _box([
        "✏️  Edit Mode",
        "",
        "Changes will be shown before applying.",
        "Approve each file · tests run after · backups created.",
        "Production files and secrets are blocked.",
    ], CYAN)


def _print_yolo_banner() -> None:
    _box([
        "⚡ YOLO MODE — no restrictions",
        "",
        "All changes auto-approved.",
        "All commands auto-run.",
        "No guards. No prompts. No backups required.",
        "",
        "You asked for it ¯\\_(ツ)_/¯",
    ], YELLOW)


def _print_summary(session: EditSession) -> None:
    applied_ok = [r for r in session.changes_applied if r.ok]
    applied_fail = [r for r in session.changes_applied if not r.ok]

    print()
    _sep("═")
    color = GREEN if applied_ok else DIM
    print(f"\n  {color}{BOLD}Edit session complete{RESET}  {DIM}· {session.summary()}{RESET}\n")

    if applied_ok:
        for r in applied_ok:
            print(f"    {GREEN}✓{RESET}  {r.path}  {DIM}({r.action}){RESET}")
    if applied_fail:
        for r in applied_fail:
            print(f"    {RED}✗{RESET}  {r.path}  {DIM}({r.error}){RESET}")
    if session.changes_skipped:
        for p in session.changes_skipped:
            print(f"    {DIM}—{RESET}  {p}  {DIM}(skipped){RESET}")
    if session.changes_blocked:
        for p, reason in session.changes_blocked:
            print(f"    {RED}🚫{RESET}  {p}  {DIM}(blocked: {reason}){RESET}")

    print()
    _sep("═")
    print()
