"""Token usage tracker — counts tokens per agent, per task, per session.

Sources:
  Ollama      — actual counts from API (prompt_eval_count, eval_count)
  LiteLLM     — actual counts from usage.prompt_tokens / completion_tokens
  CLI agents  — estimated from prompt/output char count (÷ 4, the common heuristic)

All callers record via:
    from .token_tracker import track
    track("claude",  prompt_tokens=800, completion_tokens=1200, source="estimated")
    track("ollama",  prompt_tokens=512, completion_tokens=340,  source="actual")

Print per-task summary:
    from .token_tracker import print_task_summary, reset_task
    print_task_summary()
    reset_task()

Print session totals (for REPL):
    from .token_tracker import print_session_summary
    print_session_summary()
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import DefaultDict

# ─────────────────────────────────────────────────────────────────────────────

RESET   = "\033[0m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
CYAN    = "\033[36m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
MAGENTA = "\033[35m"


@dataclass
class AgentUsage:
    agent: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0
    source: str = "estimated"   # "actual" | "estimated"

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def cost_usd_estimate(self) -> float | None:
        """Very rough estimate — only for known API models, None for local/CLI."""
        # All local or CLI — no cost
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Global state — two levels: current task + full session
# ─────────────────────────────────────────────────────────────────────────────

# task-level (reset between tasks)
_task_usage:    DefaultDict[str, AgentUsage] = defaultdict(lambda: AgentUsage(""))
_task_start:    float = time.time()

# session-level (reset on process start, accumulated across tasks in REPL)
_session_usage: DefaultDict[str, AgentUsage] = defaultdict(lambda: AgentUsage(""))
_session_tasks: int = 0


def track(
    agent: str,
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    source: str = "estimated",
) -> None:
    """Record token usage for an agent. Call after every LLM/CLI agent call."""
    for store in (_task_usage, _session_usage):
        if store[agent].agent == "":
            store[agent].agent = agent
        store[agent].prompt_tokens     += prompt_tokens
        store[agent].completion_tokens += completion_tokens
        store[agent].calls             += 1
        if source == "actual":
            store[agent].source = "actual"


def estimate_and_track(agent: str, prompt: str, output: str) -> None:
    """Estimate token count from text length and record. 1 token ≈ 4 chars."""
    p_tok = max(1, len(prompt)  // 4)
    c_tok = max(1, len(output)  // 4)
    track(agent, prompt_tokens=p_tok, completion_tokens=c_tok, source="estimated")


def reset_task() -> None:
    """Call between tasks in REPL to start fresh task-level counters."""
    global _task_usage, _task_start, _session_tasks
    _task_usage = defaultdict(lambda: AgentUsage(""))
    _task_start = time.time()
    _session_tasks += 1


# ─────────────────────────────────────────────────────────────────────────────
# Display
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}k"
    return str(n)


def _bar(value: int, total: int, width: int = 12) -> str:
    if total == 0:
        return " " * width
    filled = round(value / total * width)
    return "█" * filled + "░" * (width - filled)


def print_task_summary(label: str = "") -> None:
    """Print per-agent token table for the current task."""
    entries = [u for u in _task_usage.values() if u.total_tokens > 0]
    if not entries:
        return

    entries.sort(key=lambda u: u.total_tokens, reverse=True)
    total_all = sum(u.total_tokens for u in entries)
    elapsed = time.time() - _task_start

    title = f"Token usage{f' — {label}' if label else ''}"
    print(f"\n  {BOLD}{CYAN}{title}{RESET}  {DIM}({elapsed:.1f}s){RESET}")
    print(f"  {DIM}{'Agent':<14} {'In':>7} {'Out':>7} {'Total':>8}  {'Share':>5}  {'Source'}{RESET}")
    print(f"  {DIM}{'─'*14} {'─'*7} {'─'*7} {'─'*8}  {'─'*5}  {'─'*9}{RESET}")

    for u in entries:
        pct = u.total_tokens / total_all * 100 if total_all else 0
        src_icon = "✓" if u.source == "actual" else "~"
        bar = _bar(u.total_tokens, total_all, 10)
        print(
            f"  {CYAN}{u.agent:<14}{RESET}"
            f" {DIM}{_fmt_tokens(u.prompt_tokens):>7}{RESET}"
            f" {GREEN}{_fmt_tokens(u.completion_tokens):>7}{RESET}"
            f" {BOLD}{_fmt_tokens(u.total_tokens):>8}{RESET}"
            f"  {pct:>4.0f}%"
            f"  {DIM}{src_icon} {bar}{RESET}"
        )

    print(f"  {DIM}{'─'*60}{RESET}")
    print(
        f"  {'Total':<14}"
        f" {_fmt_tokens(sum(u.prompt_tokens for u in entries)):>7}"
        f" {_fmt_tokens(sum(u.completion_tokens for u in entries)):>7}"
        f" {BOLD}{_fmt_tokens(total_all):>8}{RESET}"
    )
    print(f"  {DIM}~ = estimated (CLI agent)  ✓ = actual (Ollama/API){RESET}\n")


def print_session_summary() -> None:
    """Print cumulative token usage across all tasks in the REPL session."""
    entries = [u for u in _session_usage.values() if u.total_tokens > 0]
    if not entries:
        return

    entries.sort(key=lambda u: u.total_tokens, reverse=True)
    total_all = sum(u.total_tokens for u in entries)

    print(f"\n  {BOLD}{MAGENTA}Session token usage  {DIM}({_session_tasks} task(s)){RESET}")
    print(f"  {DIM}{'Agent':<14} {'Calls':>5} {'Total':>8}  {'Prompt':>8}  {'Output':>8}{RESET}")
    print(f"  {DIM}{'─'*56}{RESET}")

    for u in entries:
        print(
            f"  {CYAN}{u.agent:<14}{RESET}"
            f" {DIM}{u.calls:>5}x{RESET}"
            f" {BOLD}{_fmt_tokens(u.total_tokens):>8}{RESET}"
            f"  {DIM}{_fmt_tokens(u.prompt_tokens):>8}{RESET}"
            f"  {GREEN}{_fmt_tokens(u.completion_tokens):>8}{RESET}"
        )

    print(f"  {DIM}{'─'*56}{RESET}")
    print(f"  {'Session total':<14} {'':>5} {BOLD}{_fmt_tokens(total_all):>8}{RESET}\n")


def task_total() -> int:
    return sum(u.total_tokens for u in _task_usage.values())


def session_total() -> int:
    return sum(u.total_tokens for u in _session_usage.values())
