"""Interactive REPL — run devm in a persistent session.

Usage:
    devm              # enters REPL if no task given
    devm --repl       # always enter REPL

Inside the REPL:
    > fix the webhook bug                     → routes + shows handoff
    > --a2a fix the webhook bug               → A2A council
    > --council add null guards               → sequential council
    > --edit --a2a fix type errors            → A2A + apply edits
    > --yolo --a2a refactor auth module       → full auto
    > --agent claude debug the login flow     → direct agent
    > --solve explain the payment module      → LLM direct answer
    > mode a2a                                → set default mode for session
    > mode council
    > mode edit                               → toggle edit mode on/off
    > mode yolo                               → toggle yolo on/off
    > clear                                   → clear screen
    > history                                 → show recent tasks this session
    > exit / quit / Ctrl+C / Ctrl+D           → exit
"""
from __future__ import annotations

import os
import readline
import shlex
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# ─────────────────────────────────────────────────────────────────────────────
# Colors
# ─────────────────────────────────────────────────────────────────────────────

RESET   = "\033[0m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
RED     = "\033[31m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
CYAN    = "\033[36m"
MAGENTA = "\033[35m"
WHITE   = "\033[97m"


# ─────────────────────────────────────────────────────────────────────────────
# Session state
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ReplSession:
    repo: str
    default_mode: str = ""        # "" | "a2a" | "council" | "solve" | "agent"
    edit_mode: bool = False
    yolo_mode: bool = False
    provider: str = ""
    model: str = ""
    agent: str = ""
    history: list[dict] = field(default_factory=list)  # {task, mode, elapsed, ok}

    def mode_label(self) -> str:
        parts = []
        if self.default_mode:
            parts.append(self.default_mode)
        if self.edit_mode:
            parts.append("edit")
        if self.yolo_mode:
            parts.append("yolo")
        return "+".join(parts) if parts else "route"

    def add_history(self, task: str, mode: str, elapsed: float, ok: bool) -> None:
        self.history.append({
            "task": task[:80],
            "mode": mode,
            "elapsed": round(elapsed, 1),
            "ok": ok,
        })


# ─────────────────────────────────────────────────────────────────────────────
# Banner
# ─────────────────────────────────────────────────────────────────────────────

def _print_banner(session: ReplSession) -> None:
    repo_name = Path(session.repo).name
    print(f"\n{BOLD}{CYAN}⚡ Devm  {DIM}—  interactive session{RESET}")
    print(f"  {DIM}repo:{RESET} {repo_name}   {DIM}mode:{RESET} {CYAN}{session.mode_label()}{RESET}")
    print(f"  {DIM}Type a task, or:{RESET}")
    print(f"  {DIM}  --a2a task     → agents talk to each other{RESET}")
    print(f"  {DIM}  --council task → sequential pipeline{RESET}")
    print(f"  {DIM}  --edit task    → apply file changes (with review){RESET}")
    print(f"  {DIM}  --yolo task    → apply everything instantly{RESET}")
    print(f"  {DIM}  mode a2a       → set default mode{RESET}")
    print(f"  {DIM}  history        → recent tasks{RESET}")
    print(f"  {DIM}  exit           → quit{RESET}")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Prompt line
# ─────────────────────────────────────────────────────────────────────────────

def _build_prompt(session: ReplSession) -> str:
    mode = session.mode_label()
    repo_name = Path(session.repo).name
    mode_color = CYAN if mode != "route" else DIM
    yolo_tag = f" {YELLOW}⚡{RESET}" if session.yolo_mode else ""
    edit_tag = f" {GREEN}✏️{RESET}" if session.edit_mode and not session.yolo_mode else ""
    return (
        f"{DIM}{repo_name}{RESET} "
        f"{mode_color}({mode}){RESET}"
        f"{edit_tag}{yolo_tag} "
        f"{BOLD}{CYAN}❯{RESET} "
    )


# ─────────────────────────────────────────────────────────────────────────────
# Command parsing
# ─────────────────────────────────────────────────────────────────────────────

def _parse_line(line: str, session: ReplSession) -> dict:
    """Parse user input into {task, flags}. Handles inline flags like --a2a."""
    line = line.strip()

    # REPL meta-commands
    lower = line.lower()
    if lower in ("exit", "quit", "q", ":q"):
        return {"meta": "exit"}
    if lower in ("clear", "cls"):
        return {"meta": "clear"}
    if lower == "history":
        return {"meta": "history"}
    if lower in ("help", "?"):
        return {"meta": "help"}
    if lower.startswith("mode "):
        return {"meta": "mode", "value": lower[5:].strip()}
    if lower == "agents":
        return {"meta": "agents"}
    if lower.startswith("repo "):
        return {"meta": "repo", "value": line[5:].strip()}

    # Parse flags inline: --a2a, --council, --edit, --yolo, --solve, --agent X
    flags = {
        "a2a": session.default_mode == "a2a",
        "council": session.default_mode == "council",
        "solve": session.default_mode == "solve",
        "edit": session.edit_mode,
        "yolo": session.yolo_mode,
        "agent": session.agent if session.default_mode == "agent" else "",
        "bg": False,
    }

    tokens = line.split()
    task_tokens = []
    i = 0
    while i < len(tokens):
        tok = tokens[i].lower()
        if tok == "--a2a":
            flags["a2a"] = True
            flags["council"] = False
            flags["solve"] = False
        elif tok == "--council":
            flags["council"] = True
            flags["a2a"] = False
            flags["solve"] = False
        elif tok == "--solve":
            flags["solve"] = True
            flags["a2a"] = False
            flags["council"] = False
        elif tok == "--edit":
            flags["edit"] = True
            flags["yolo"] = False
        elif tok == "--yolo":
            flags["yolo"] = True
            flags["edit"] = False
        elif tok == "--bg":
            flags["bg"] = True
        elif tok == "--agent":
            i += 1
            if i < len(tokens):
                flags["agent"] = tokens[i]
                flags["a2a"] = False
                flags["council"] = False
                flags["solve"] = False
        else:
            task_tokens.append(tokens[i])
        i += 1

    task = " ".join(task_tokens).strip()
    if not task:
        return {"meta": "empty"}

    return {"task": task, "flags": flags}


# ─────────────────────────────────────────────────────────────────────────────
# Execute one task
# ─────────────────────────────────────────────────────────────────────────────

def _run_task(task: str, flags: dict, session: ReplSession, llm_cfg: dict) -> bool:
    """Run a single task. Returns True if successful."""
    from .context import collect_context
    from .router import classify_task
    from .handoff import build_handoff
    from .state import save_run_record

    repo_root = Path(session.repo)
    context = collect_context(repo_root)
    local_route = classify_task(task, context)

    from .llm import ask as ask_llm
    llm_route = ask_llm(task=task, context=context, local_route=local_route, cfg=llm_cfg)
    route = llm_route or local_route

    handoff = build_handoff(task=task, route=route, context=context, used_llm=bool(llm_route))

    start = time.time()
    ok = True

    # ── A2A ──
    if flags.get("a2a"):
        from .a2a import A2ACouncil
        a2a = A2ACouncil(
            task=task, repo=str(repo_root),
            handoff_prompt=handoff["prompt"],
            cfg=llm_cfg, interactive=True,
        )
        s = a2a.run()
        combined = s.chat_log()
        if flags.get("edit") or flags.get("yolo"):
            from .edit_mode import run_edit_mode
            run_edit_mode(combined, str(repo_root), yolo=flags.get("yolo", False))

    # ── Council ──
    elif flags.get("council"):
        from .interactive import InteractiveCouncil
        council = InteractiveCouncil(
            task=task, repo=str(repo_root),
            handoff_prompt=handoff["prompt"],
            cfg=llm_cfg, interactive=True,
        )
        s = council.run()
        transcript = "\n".join(f"## {t.role.upper()}\n{t.output}" for t in s.turns)
        if flags.get("edit") or flags.get("yolo"):
            from .edit_mode import run_edit_mode
            run_edit_mode(transcript, str(repo_root), yolo=flags.get("yolo", False))

    # ── Single agent ──
    elif flags.get("agent"):
        from .interactive import run_agent_interactive, C
        from .agent_bridge import agent_for_owner as bridge_agent_for_owner, available_agents
        agent_key = flags["agent"]
        if agent_key == "auto":
            agent_key = bridge_agent_for_owner(handoff["owner"])
            if not agent_key:
                avail = available_agents()
                agent_key = next(iter(avail), None)
        if not agent_key:
            print(f"  {RED}No agent found. Run 'agents' to check.{RESET}")
            return False
        result = run_agent_interactive(
            agent_key, handoff["prompt"], role="analyst",
            repo=str(repo_root), interactive=True,
        )
        ok = result["ok"]
        if ok and (flags.get("edit") or flags.get("yolo")):
            from .edit_mode import run_edit_mode
            run_edit_mode(result.get("output", ""), str(repo_root), yolo=flags.get("yolo", False))

    # ── Solve (direct LLM) ──
    elif flags.get("solve"):
        from .solver import solve, check_provider
        ok_prov, msg = check_provider(llm_cfg)
        if not ok_prov:
            print(f"  {RED}Provider not ready: {msg}{RESET}")
            return False
        solve(handoff["prompt"], cfg=llm_cfg, stream=True)

    # ── Default: show handoff ──
    else:
        from .cli import _print_handoff
        _print_handoff(handoff)

    elapsed = time.time() - start
    session.add_history(task, _mode_name(flags), elapsed, ok)
    save_run_record(task=task, repo=str(repo_root),
                    evidence={"handoff": handoff},
                    compact={"task": task, "mode": _mode_name(flags)},
                    gui_result=None)
    return ok


def _mode_name(flags: dict) -> str:
    if flags.get("a2a"):
        return "a2a" + ("+edit" if flags.get("edit") else "+yolo" if flags.get("yolo") else "")
    if flags.get("council"):
        return "council" + ("+edit" if flags.get("edit") else "+yolo" if flags.get("yolo") else "")
    if flags.get("agent"):
        return f"agent:{flags['agent']}"
    if flags.get("solve"):
        return "solve"
    return "route"


# ─────────────────────────────────────────────────────────────────────────────
# Meta-command handlers
# ─────────────────────────────────────────────────────────────────────────────

def _handle_meta(cmd: dict, session: ReplSession) -> bool:
    """Handle REPL meta-commands. Returns False to exit, True to continue."""
    meta = cmd["meta"]

    if meta == "exit":
        _print_goodbye(session)
        return False

    if meta == "clear":
        os.system("clear")
        _print_banner(session)
        return True

    if meta == "empty":
        return True

    if meta == "help":
        _print_help(session)
        return True

    if meta == "agents":
        from .agent_bridge import print_agents
        print_agents(show_all=False)
        return True

    if meta == "repo":
        new_repo = Path(cmd["value"]).expanduser().resolve()
        if not new_repo.is_dir():
            print(f"  {RED}Directory not found: {new_repo}{RESET}")
        else:
            session.repo = str(new_repo)
            print(f"  {GREEN}✓  repo → {new_repo.name}{RESET}")
        return True

    if meta == "history":
        _print_history(session)
        return True

    if meta == "mode":
        value = cmd["value"]
        if value == "a2a":
            session.default_mode = "a2a"
            print(f"  {GREEN}✓  default mode → a2a  {DIM}(agents talk to each other){RESET}")
        elif value == "council":
            session.default_mode = "council"
            print(f"  {GREEN}✓  default mode → council  {DIM}(sequential pipeline){RESET}")
        elif value == "solve":
            session.default_mode = "solve"
            print(f"  {GREEN}✓  default mode → solve  {DIM}(direct LLM answer){RESET}")
        elif value == "route":
            session.default_mode = ""
            print(f"  {GREEN}✓  default mode → route  {DIM}(smart routing){RESET}")
        elif value == "edit":
            session.edit_mode = not session.edit_mode
            state = "on" if session.edit_mode else "off"
            print(f"  {GREEN}✓  edit mode → {state}{RESET}")
        elif value == "yolo":
            session.yolo_mode = not session.yolo_mode
            state = "on" if session.yolo_mode else "off"
            tag = f"  {YELLOW}⚡ No restrictions, all changes auto-approved{RESET}" if session.yolo_mode else ""
            print(f"  {GREEN}✓  yolo mode → {state}{RESET}{tag}")
        elif value.startswith("agent "):
            session.default_mode = "agent"
            session.agent = value[6:].strip()
            print(f"  {GREEN}✓  default mode → agent:{session.agent}{RESET}")
        else:
            print(f"  {DIM}Modes: a2a · council · solve · route · edit · yolo · agent <name>{RESET}")
        return True

    return True


# ─────────────────────────────────────────────────────────────────────────────
# Printing helpers
# ─────────────────────────────────────────────────────────────────────────────

def _print_history(session: ReplSession) -> None:
    if not session.history:
        print(f"  {DIM}No tasks yet this session.{RESET}")
        return
    print(f"\n  {BOLD}Session history:{RESET}")
    for i, h in enumerate(reversed(session.history[-15:]), 1):
        ok_icon = f"{GREEN}✓{RESET}" if h["ok"] else f"{RED}✗{RESET}"
        print(f"  {ok_icon}  {DIM}{i:2}.{RESET}  {h['task']}  "
              f"{DIM}[{h['mode']} · {h['elapsed']}s]{RESET}")
    print()


def _print_help(session: ReplSession) -> None:
    print(f"""
  {BOLD}Commands:{RESET}

  {CYAN}<task>{RESET}               → route and show handoff
  {CYAN}--a2a <task>{RESET}         → A2A council (agents @mention each other)
  {CYAN}--council <task>{RESET}     → sequential pipeline
  {CYAN}--solve <task>{RESET}       → direct LLM answer (no GUI)
  {CYAN}--agent auto <task>{RESET}  → best installed CLI agent
  {CYAN}--edit <task>{RESET}        → run + apply file changes (with review)
  {CYAN}--yolo <task>{RESET}        → run + apply everything (no prompts)
  {CYAN}--bg <task>{RESET}          → run in background

  {BOLD}Combine flags:{RESET}
  {DIM}--a2a --edit <task>{RESET}   → A2A council then apply changes with review
  {DIM}--a2a --yolo <task>{RESET}   → A2A council then apply everything instantly

  {BOLD}Session settings:{RESET}
  {CYAN}mode a2a{RESET}             → make a2a the default for this session
  {CYAN}mode council{RESET}
  {CYAN}mode edit{RESET}            → toggle edit mode on/off (applies to all tasks)
  {CYAN}mode yolo{RESET}            → toggle yolo mode on/off
  {CYAN}mode route{RESET}           → reset to smart routing
  {CYAN}repo /path/to/project{RESET} → switch project

  {CYAN}history{RESET}              → recent tasks this session
  {CYAN}agents{RESET}               → show discovered AI CLIs
  {CYAN}clear{RESET}                → clear screen
  {CYAN}exit{RESET}                 → quit
""")


def _print_goodbye(session: ReplSession) -> None:
    n = len(session.history)
    print(f"\n  {DIM}Session ended · {n} task(s) run{RESET}\n")


def _print_task_result(ok: bool, elapsed: float) -> None:
    if ok:
        print(f"\n  {GREEN}✓  Done{RESET}  {DIM}({elapsed:.1f}s){RESET}\n")
    else:
        print(f"\n  {RED}✗  Failed{RESET}  {DIM}({elapsed:.1f}s){RESET}\n")


# ─────────────────────────────────────────────────────────────────────────────
# Readline history
# ─────────────────────────────────────────────────────────────────────────────

_HISTORY_FILE = Path.home() / ".devmanager" / ".repl_history"

def _setup_readline() -> None:
    try:
        readline.set_history_length(500)
        if _HISTORY_FILE.exists():
            readline.read_history_file(str(_HISTORY_FILE))
        # Tab completion stubs
        readline.parse_and_bind("tab: complete")
    except Exception:
        pass

def _save_readline() -> None:
    try:
        _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        readline.write_history_file(str(_HISTORY_FILE))
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Main REPL loop
# ─────────────────────────────────────────────────────────────────────────────

def run_repl(repo: str, llm_cfg: dict) -> None:
    session = ReplSession(repo=repo)
    _setup_readline()
    _print_banner(session)

    while True:
        try:
            prompt = _build_prompt(session)
            line = input(prompt)
        except KeyboardInterrupt:
            print(f"\n  {DIM}(Ctrl+C — type 'exit' to quit){RESET}")
            continue
        except EOFError:
            # Ctrl+D
            _print_goodbye(session)
            break

        cmd = _parse_line(line, session)

        if "meta" in cmd:
            should_continue = _handle_meta(cmd, session)
            if not should_continue:
                break
            continue

        # Run the task
        task = cmd["task"]
        flags = cmd["flags"]
        mode = _mode_name(flags)

        print(f"\n  {DIM}Task: {task[:70]}{RESET}  {DIM}[{mode}]{RESET}\n")

        start = time.time()
        try:
            ok = _run_task(task, flags, session, llm_cfg)
        except KeyboardInterrupt:
            elapsed = time.time() - start
            print(f"\n  {YELLOW}⚠  Interrupted{RESET}  {DIM}({elapsed:.1f}s){RESET}")
            session.add_history(task, mode, elapsed, False)
            ok = False
        except Exception as exc:
            elapsed = time.time() - start
            print(f"\n  {RED}✗  Error: {exc}{RESET}")
            session.add_history(task, mode, elapsed, False)
            ok = False
        else:
            elapsed = time.time() - start
            _print_task_result(ok, elapsed)

    _save_readline()
