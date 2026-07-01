"""Unified CLI — merges direct-Ollama path and Google ADK runner path."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from .agent_config import list_roles, load_agent_config, load_profiles_config
from .consult import print_consult
from .context import collect_context
from .doctor import build_doctor_report, print_doctor_report
from .gui_bridge import send_to_gui_app
from .handoff import build_handoff
from .agent_bridge import run_agent, agent_for_owner as bridge_agent_for_owner, print_agents, discover_agents, available_agents
from .council import Council, run_adk_council
from .jobs import spawn_background, print_jobs, print_result, delete_job, list_jobs
from .llm import ask as ask_llm
from .repair import print_repair
from .router import classify_task
from .safe_runner import run_safe_commands
from .solver import solve, check_provider
from .state import save_run_record
from .user_config import load as load_user_config, print_config, save as save_user_config, PROVIDERS


def main(argv: list[str] | None = None) -> int:
    # Subcommand dispatch (before full argparse so `devm consult "..."` works cleanly)
    raw = argv if argv is not None else sys.argv[1:]
    if raw and raw[0] == "consult":
        query = " ".join(raw[1:]).strip()
        if not query:
            print("Usage: devm consult \"what you want to do\"")
            return 2
        print_consult(query)
        return 0
    if raw and raw[0] == "repair":
        dry = "--dry-run" in raw
        print_repair(dry_run=dry)
        return 0
    if raw and raw[0] == "update":
        return _run_update()
    if raw and raw[0] == "config":
        return _run_config(raw[1:])
    if raw and raw[0] == "history":
        return _run_history(raw[1:])
    if raw and raw[0] == "jobs":
        return _run_jobs(raw[1:])
    if raw and raw[0] == "result":
        return _run_result(raw[1:])
    if raw and raw[0] == "agents":
        show_all = "--all" in raw
        print_agents(show_all=show_all)
        return 0

    if raw and raw[0] == "agent-add":
        return _run_agent_add_wizard()


    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.doctor:
        print_doctor_report(build_doctor_report(repo=args.repo, model=args.model), as_json=args.json)
        return 0
    if args.list_agents:
        _print_agents(args.json)
        return 0
    if args.list_roles:
        _print_roles(args.json)
        return 0
    if args.list_profiles:
        _print_profiles(args.json)
        return 0

    task = " ".join(args.task).strip()
    if not task and not sys.stdin.isatty():
        task = sys.stdin.read().strip()

    # No task given (or --repl flag) → enter interactive REPL
    if not task or getattr(args, "repl", False):
        from .repl import run_repl
        user_cfg = load_user_config()
        llm_cfg = {**user_cfg}
        if args.provider:
            llm_cfg["provider"] = args.provider
        if args.model != "glm4:latest":
            llm_cfg["model"] = args.model
        if args.api_key:
            llm_cfg["api_key"] = args.api_key
        repo_root = str(Path(args.repo).expanduser().resolve())
        run_repl(repo=repo_root, llm_cfg=llm_cfg)
        return 0

    if args.no_submit:
        args.submit = False

    if args.adk:
        return asyncio.run(_run_adk(args, task))
    return _run_direct(args, task)


# ---------------------------------------------------------------------------
# Direct path: local rules + optional Ollama
# ---------------------------------------------------------------------------

def _run_direct(args: argparse.Namespace, task: str) -> int:
    repo_root = Path(args.repo).expanduser().resolve()
    context = collect_context(repo_root)
    local_route = classify_task(task, context)

    # Build effective LLM config: user config < env vars < CLI flags
    user_cfg = load_user_config()
    llm_cfg = {**user_cfg}
    if args.provider:
        llm_cfg["provider"] = args.provider
    if args.model != "glm4:latest":  # user explicitly set --model
        llm_cfg["model"] = args.model
    if args.api_key:
        llm_cfg["api_key"] = args.api_key
    if args.ollama_url != "http://127.0.0.1:11434":
        llm_cfg["ollama_url"] = args.ollama_url
        llm_cfg["base_url"] = args.ollama_url

    llm_route = None
    if not args.no_llm:
        llm_route = ask_llm(task=task, context=context, local_route=local_route, cfg=llm_cfg)
        if args.require_llm and not llm_route:
            provider = llm_cfg.get("provider", "ollama")
            info = PROVIDERS.get(provider, {})
            print(_unavailable_message(provider, llm_cfg.get("model", "?"), info))
            return 3

    route = llm_route or local_route
    handoff = build_handoff(
        task=task,
        route=route,
        context=context,
        used_llm=bool(llm_route),
        profile=args.profile,
        role=args.role,
    )

    # ── Background mode ───────────────────────────────────────────────────
    if args.bg:
        extra = []
        if args.profile:
            extra += ["--profile", args.profile]
        if args.role:
            extra += ["--role", args.role]
        if args.no_llm:
            extra.append("--no-llm")
        if args.provider:
            extra += ["--provider", args.provider]
        if args.model and args.model != "glm4:latest":
            extra += ["--model", args.model]
        if args.agent:
            extra += ["--agent", args.agent]
        if getattr(args, "a2a", False):
            extra.append("--a2a")
        if args.council:
            extra.append("--council")
        if args.adk_council:
            extra.append("--adk-council")
        job_id = spawn_background(task, str(repo_root), extra_args=extra)
        BOLD = "\033[1m"; GREEN = "\033[32m"; DIM = "\033[2m"; RESET = "\033[0m"
        print(f"\n{GREEN}✓ Background job started{RESET}")
        print(f"  Job ID:  {BOLD}{job_id}{RESET}")
        print(f"  {DIM}devm result {job_id[:20]}   # check result{RESET}")
        print(f"  {DIM}devm jobs                  # list all jobs{RESET}\n")
        return 0

    run_results = []
    if args.run_safe:
        run_results = run_safe_commands(handoff.get("safe_commands", []), cwd=repo_root)
        handoff["safe_command_results"] = run_results

    if args.json:
        print(json.dumps(handoff, indent=2, ensure_ascii=False))
    else:
        _print_handoff(handoff)

    # ── Council mode: interactive multi-agent pipeline ────────────────────
    # ── A2A mode: agents genuinely talk to each other ────────────────────
    if getattr(args, "a2a", False):
        from .a2a import A2ACouncil
        a2a = A2ACouncil(
            task=task,
            repo=str(repo_root),
            handoff_prompt=handoff["prompt"],
            cfg=llm_cfg,
            interactive=True,
        )
        session = a2a.run()
        combined_output = session.chat_log()
        save_run_record(
            task=task, repo=str(repo_root),
            evidence={"handoff": handoff, "a2a_chat": combined_output},
            compact=_compact_handoff(handoff), gui_result=None,
        )
        # Edit/Yolo: apply file changes from agent output
        if getattr(args, "edit", False) or getattr(args, "yolo", False):
            from .edit_mode import run_edit_mode
            run_edit_mode(combined_output, str(repo_root), yolo=getattr(args, "yolo", False))
        return 0

    if args.council:
        from .interactive import InteractiveCouncil
        council = InteractiveCouncil(
            task=task,
            repo=str(repo_root),
            handoff_prompt=handoff["prompt"],
            cfg=llm_cfg,
            interactive=True,
        )
        session = council.run()
        transcript = "\n".join(
            f"## {t.role.upper()} ({t.agent})\n{t.output}"
            for t in session.turns
        )
        save_run_record(
            task=task, repo=str(repo_root),
            evidence={"handoff": handoff, "council_transcript": transcript},
            compact=_compact_handoff(handoff), gui_result=None,
        )
        # Edit/Yolo: apply file changes from agent output
        if getattr(args, "edit", False) or getattr(args, "yolo", False):
            from .edit_mode import run_edit_mode
            run_edit_mode(transcript, str(repo_root), yolo=getattr(args, "yolo", False))
        return 0

    # ── ADK Council mode: GLM4 orchestrates agents via ADK ────────────────
    if args.adk_council:
        try:
            import asyncio
            asyncio.run(run_adk_council(task, str(repo_root), handoff["prompt"], cfg=llm_cfg))
        except RuntimeError as exc:
            print(f"\n[adk-council] {exc}", file=sys.stderr)
            print("Falling back to --council mode…")
            from .interactive import InteractiveCouncil
            InteractiveCouncil(task=task, repo=str(repo_root),
                               handoff_prompt=handoff["prompt"], cfg=llm_cfg).run()
        return 0

    # ── Agent mode: single agent with interactive streaming ──────────────
    if args.agent:
        from .interactive import run_agent_interactive, ROLE_STYLE, C
        agent_key = args.agent
        if agent_key == "auto":
            agent_key = bridge_agent_for_owner(handoff["owner"])
            if not agent_key:
                avail = available_agents()
                agent_key = next(iter(avail), None)
            if not agent_key:
                print("[agent] No installed CLI agents found. Run 'devm agents' to check.", file=sys.stderr)
                return 3
        result = run_agent_interactive(
            agent_key, handoff["prompt"], role="analyst",
            repo=str(repo_root), interactive=True,
        )
        if not result["ok"]:
            print(f"\n{C['red']}[agent] {agent_key} failed (exit {result.get('returncode', '?')}){C['reset']}")
            if result.get("error"):
                print(result["error"][:500])
        handoff["agent_result"] = result
        save_run_record(task=task, repo=str(repo_root),
                        evidence={"handoff": handoff},
                        compact=_compact_handoff(handoff), gui_result=None)
        # Edit/Yolo: apply file changes from agent output
        if result["ok"] and (getattr(args, "edit", False) or getattr(args, "yolo", False)):
            from .edit_mode import run_edit_mode
            run_edit_mode(result.get("output", ""), str(repo_root), yolo=getattr(args, "yolo", False))
        return 0 if result["ok"] else 1

    # ── Solve mode: call LLM directly, stream answer ──────────────────────
    if args.solve:
        ok, msg = check_provider(llm_cfg)
        if not ok:
            print(f"\n[solve] Provider not ready: {msg}", file=sys.stderr)
            return 3
        BOLD = "\033[1m"; DIM = "\033[2m"; CYAN = "\033[36m"; RESET = "\033[0m"
        owner = handoff.get("owner", "?")
        target = handoff.get("target_app", "?")
        provider_name = llm_cfg.get("provider", "ollama")
        model_name = llm_cfg.get("model", "?")
        print(f"\n{BOLD}╭─ Autonomous Solve {'─' * 33}╮{RESET}")
        print(f"{BOLD}│{RESET}  Owner: {CYAN}{target}{RESET}  Provider: {provider_name}/{model_name}")
        print(f"{BOLD}╰{'─' * 52}╯{RESET}\n")
        result_text = solve(handoff["prompt"], cfg=llm_cfg, stream=True)
        handoff["solve_result"] = result_text
        save_run_record(
            task=task,
            repo=str(repo_root),
            evidence={"handoff": handoff},
            compact=_compact_handoff(handoff),
            gui_result=None,
        )
        return 0

    gui_result = None
    gui_target = args.gui or (handoff.get("target_app") if args.auto_gui else None)
    if gui_target:
        gui_result = send_to_gui_app(gui_target, handoff["prompt"], submit=args.submit)
        print("\n=== GUI Bridge ===")
        print(json.dumps(gui_result, indent=2, ensure_ascii=False))

    save_run_record(
        task=task,
        repo=str(repo_root),
        evidence={"handoff": handoff},
        compact=_compact_handoff(handoff),
        gui_result=gui_result,
    )
    return 0


# ---------------------------------------------------------------------------
# ADK path: Google ADK runner + local GLM
# ---------------------------------------------------------------------------

async def _run_adk(args: argparse.Namespace, task: str) -> int:
    try:
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from google.genai import types
        from .adk_agent import build_agent
        from .adk_tools import route_task as adk_route_task, search_code
    except ImportError:
        print("google-adk not installed. Run:  pip install google-adk\nOr use without --adk flag.")
        return 1

    os.environ.setdefault("OLLAMA_API_BASE", args.ollama_url)
    os.environ.setdefault("LITELLM_LOG", "ERROR")

    repo = str(Path(args.repo).expanduser().resolve())
    handoff = adk_route_task(repo, task, profile=args.profile or "", role=args.role or "")

    terms = _terms_for_task(task)
    searches = []
    for term in terms[:8]:
        result = search_code(repo, term)
        searches.append({
            "term": term,
            "returncode": result["returncode"],
            "output": result["output"][:700],
            "truncated": result["truncated"],
        })

    evidence = {"repo": repo, "task": task, "handoff": handoff, "searches": searches}
    compact = _compact_evidence(evidence)

    gui_target = args.gui or (compact.get("target_app") if args.auto_gui else None)
    gui_line = (
        f"\nA GUI handoff will be pasted into {gui_target} after your final answer."
        if gui_target else ""
    )

    prompt = (
        f"Repo root: {repo}\n\nUser task:\n{task}\n\n"
        "Manager mode:\n"
        "- Use only the evidence JSON below as observed facts.\n"
        "- Do not invent files, command output, or grep results.\n"
        "- Do not suggest commands outside suggested_safe_commands.\n"
        f"{gui_line}\n\n"
        f"Evidence JSON:\n{json.dumps(compact, indent=2, ensure_ascii=False)}"
    )

    app_name = "local_dev_manager"
    user_id = "local-user"
    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name=app_name, user_id=user_id)
    runner = Runner(
        app_name=app_name,
        agent=build_agent(args.model, include_tools=False),
        session_service=session_service,
    )
    message = types.Content(role="user", parts=[types.Part.from_text(text=prompt)])

    print("=== ADK Route ===")
    print(f"Owner: {compact['owner']}  |  Target: {compact['target_app']}  |  Confidence: {compact['confidence']}")
    print(f"Reason: {compact['reason']}\n")
    print("=== GLM/ADK Analysis ===")

    async for event in runner.run_async(user_id=user_id, session_id=session.id, new_message=message):
        content = getattr(event, "content", None)
        if not content or not content.parts:
            continue
        for part in content.parts:
            text = getattr(part, "text", None)
            if text:
                print(text, end="" if text.endswith("\n") else "\n")

    print("\n=== Safe Commands ===")
    for cmd in compact.get("suggested_safe_commands", []):
        print(f"- {cmd}")

    gui_result = None
    if gui_target:
        gui_result = send_to_gui_app(gui_target, handoff["prompt"], submit=args.submit)
        print("\n=== GUI Bridge ===")
        print(json.dumps(gui_result, indent=2, ensure_ascii=False))

    report_path = save_run_record(
        task=task, repo=repo, evidence=evidence, compact=compact, gui_result=gui_result
    )
    print(f"\nSaved run: {report_path}")
    return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_config(args: list[str]) -> int:
    if not args or args[0] == "show":
        print_config()
        return 0
    if args[0] == "set":
        updates: dict = {}
        for pair in args[1:]:
            if "=" not in pair:
                print(f"Invalid: '{pair}' — use KEY=VALUE format.")
                continue
            key, _, value = pair.partition("=")
            updates[key.strip()] = value.strip()
        if not updates:
            print("Nothing to set. Example: devm config set provider=openai model=gpt-4o-mini")
            return 2
        # Validate provider
        if "provider" in updates and updates["provider"] not in PROVIDERS:
            print(f"Unknown provider: {updates['provider']}")
            print(f"Available: {', '.join(PROVIDERS)}")
            return 1
        save_user_config(updates)
        print(f"Saved: {updates}")
        print_config()
        return 0
    if args[0] == "reset":
        save_user_config({"provider": "ollama", "model": "glm4:latest", "api_key": None,
                          "base_url": "http://127.0.0.1:11434"})
        print("Config reset to default (ollama/glm4).")
        return 0
    print(f"Unknown config subcommand: {args[0]}")
    print("Usage: devm config | devm config set KEY=VALUE | devm config reset")
    return 1


def _run_jobs(args: list[str]) -> int:
    if args and args[0] == "clear":
        jobs = list_jobs(200)
        cleared = 0
        for job in jobs:
            if job.get("status") in ("done", "failed"):
                delete_job(job["id"])
                cleared += 1
        print(f"Cleared {cleared} completed/failed jobs.")
        return 0
    n = 20
    if args:
        try:
            n = int(args[0])
        except ValueError:
            pass
    print_jobs(n)
    return 0


def _run_result(args: list[str]) -> int:
    if not args:
        print("Usage: devm result <job-id>")
        print("       devm jobs  — to list job IDs")
        return 2
    return print_result(args[0])


def _run_history(args: list[str]) -> int:
    from .state import load_state, STATE_RUNS_DIR
    n = 10
    if args:
        try:
            n = int(args[0])
        except ValueError:
            pass

    state = load_state()
    runs = state.get("runs", [])
    if not runs:
        print("No runs recorded yet. Run 'devm \"some task\"' to start.")
        return 0

    BOLD = "\033[1m"; DIM = "\033[2m"; CYAN = "\033[36m"; RESET = "\033[0m"
    recent = runs[-n:][::-1]  # newest first
    print(f"\n{BOLD}Recent runs (last {len(recent)}):{RESET}\n")
    for run in recent:
        created = run.get("created_at", "?")[:16].replace("T", " ")
        task = run.get("task", "?")[:70]
        path = run.get("path", "")
        # Try to read the run file for more detail
        route_info = ""
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            r = data.get("route", {})
            route_info = f"  {CYAN}→ {r.get('target_app','?')}{RESET} ({r.get('owner','?')}, {r.get('confidence','?')})"
        except (OSError, json.JSONDecodeError):
            pass
        print(f"  {DIM}{created}{RESET}  {task}")
        if route_info:
            print(f"  {route_info}")
        print()
    return 0


def _run_update() -> int:
    repo = Path(__file__).resolve().parents[1]
    print("Updating DevManager from git...")
    pull = subprocess.run(["git", "pull"], cwd=repo, text=True, capture_output=True)
    print(pull.stdout or pull.stderr or "git pull done")
    if pull.returncode != 0:
        print(f"git pull failed (exit {pull.returncode}). Update manually.")
        return 1
    venv_pip = repo / ".venv" / "bin" / "pip"
    if not venv_pip.exists():
        venv_pip = repo / ".venv-adk" / "bin" / "pip"
    if venv_pip.exists():
        print("Reinstalling devmanager...")
        result = subprocess.run([str(venv_pip), "install", "-q", "-e", str(repo)],
                                text=True, capture_output=True)
        if result.returncode == 0:
            print("Done. Run 'devm --doctor' to verify.")
        else:
            print(result.stderr or "pip install failed")
            return 1
    else:
        print("Venv not found — run ./install.sh to set up the environment.")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="devm",
        description=(
            "DevManager — route dev tasks to the right AI agent.\n"
            "Powered by Google ADK + local Ollama GLM. No paid API keys.\n\n"
            "Subcommands:\n"
            "  devm config               → show current provider/model config\n"
            "  devm config set provider=openai model=gpt-4o-mini\n"
            "  devm config set provider=ollama model=llama3.2\n"
            "  devm config set api_key=sk-...\n"
            "  devm consult \"task\"       → skill/agent recommender\n"
            "  devm repair               → recreate missing config files\n"
            "  devm update               → git pull + pip reinstall\n"
            "  devm history [N]          → show last N runs (default 10)\n"
            "  devm jobs                 → list background jobs\n"
            "  devm jobs clear           → delete completed/failed jobs\n"
            "  devm result <job-id>      → show background job result\n\n"
            "Autonomous (no GUI needed):\n"
            "  devm --solve \"task\"              → call LLM directly, stream answer\n"
            "  devm --agent auto \"task\"         → use best installed CLI agent\n"
            "  devm --agent claude \"task\"       → force Claude Code CLI\n"
            "  devm --agent codex  \"task\"       → force Codex CLI\n\n"
            "Multi-agent Council (agents talk to each other):\n"
            "  devm --council \"task\"            → Planner→Explorer→Analyst→Reviewer→Synth\n"
            "  devm --adk-council \"task\"        → ADK orchestrated (GLM4 decides flow)\n"
            "  devm --bg --council \"task\"       → council in background\n\n"
            "Routing:\n"
            "  devm --repo /path \"payment bug fix karo\"          (uses saved config)\n"
            "  devm --provider openai --model gpt-4o \"debug\"     (override once)\n"
            "  devm --provider ollama --model llama3.2 \"task\"    (local open-source)\n"
            "  devm --provider anthropic --model claude-3-5-haiku-20241022 \"task\"\n"
            "  devm --provider groq --model llama-3.1-8b-instant \"task\"  (free)\n"
            "  devm --adk --auto-gui \"debug docker redis issue\"\n\n"
            "Info:\n"
            "  devm --doctor\n"
            "  devm --list-agents / --list-roles / --list-profiles"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("task", nargs="*", help="Task or problem to route.")
    parser.add_argument("--repo", default=str(Path.cwd()), help="Project root to inspect. Default: current directory.")
    # LLM provider flags (override saved config for this run)
    parser.add_argument("--provider", default=None,
                        help="LLM provider: ollama | openai | anthropic | gemini | groq | together | openai-compatible")
    parser.add_argument("--model", default=os.getenv("DEV_MANAGER_MODEL", "glm4:latest"),
                        help="Model name (default from saved config or glm4:latest for ollama).")
    parser.add_argument("--api-key", default=os.getenv("DEV_MANAGER_API_KEY"),
                        help="API key for paid providers (or set via env: OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.)")
    parser.add_argument("--ollama-url", default=os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"), help="Ollama server URL.")
    parser.add_argument("--profile", default=os.getenv("DEV_MANAGER_PROFILE"), help="Profile from config/profiles.json.")
    parser.add_argument("--role", help="Role preset (planner/explorer/reviewer/security/fixer/release).")
    # Mode flags
    parser.add_argument("--adk", action="store_true", help="Use Google ADK runner for enhanced GLM analysis.")
    parser.add_argument("--no-llm", action="store_true", help="Skip Ollama; use local rules only.")
    parser.add_argument("--require-llm", action="store_true",
                        default=os.getenv("DEV_MANAGER_REQUIRE_LLM") == "1",
                        help="Fail instead of falling back when Ollama is unavailable.")
    parser.add_argument("--run-safe", action="store_true", help="Run allowlisted safe verification commands.")
    # Autonomous solve flags
    parser.add_argument("--repl", action="store_true",
                        help="Enter interactive session — run multiple tasks without restarting. "
                             "Also triggered when no task is given.")
    parser.add_argument("--solve", action="store_true",
                        help="Call LLM directly with full handoff prompt — no GUI needed. Streams answer to terminal.")
    parser.add_argument("--bg", action="store_true",
                        help="Run solve in background. Returns job ID immediately. Use 'devm result <id>' to check.")
    parser.add_argument("--a2a", action="store_true",
                        help="A2A council: agents genuinely talk to each other via @mentions. Real bidirectional communication.")
    parser.add_argument("--edit", action="store_true",
                        help="Edit mode: extract file changes from agent output and apply them with permission prompts. "
                             "Runs tests after. Blocks secrets/production files.")
    parser.add_argument("--yolo", action="store_true",
                        help="YOLO mode: fully unrestricted. Auto-approve all file edits and commands. No guards, no prompts. "
                             "⚠️  Use only in throwaway branches.")
    parser.add_argument("--council", action="store_true",
                        help="Run multi-agent council: Planner→Explorer→Analyst→Reviewer→Synthesizer (sequential pipeline).")
    parser.add_argument("--adk-council", action="store_true",
                        help="ADK-orchestrated council: GLM4 decides which agents to call and when (requires google-adk).")
    parser.add_argument("--agent", default=None, metavar="NAME",
                        help="Send prompt to installed CLI agent directly: claude | codex | auto. "
                             "No GUI opened. Runs the agent CLI in your repo. "
                             "'auto' picks the best agent for the routing owner.")
    # GUI flags
    parser.add_argument("--gui", help="Paste handoff into a named GUI app (e.g. Claude, Codex, Antigravity).")
    parser.add_argument("--auto-gui", action="store_true", help="Auto-detect target GUI app from routing result.")
    parser.add_argument("--submit", action="store_true", help="Press Return after pasting into GUI app.")
    parser.add_argument("--no-submit", action="store_true", help="Do not press Return after pasting.")
    # Info flags
    parser.add_argument("--doctor", action="store_true", help="Run diagnostics and exit.")
    parser.add_argument("--list-agents", action="store_true", help="Show configured agents.")
    parser.add_argument("--list-roles", action="store_true", help="Show configured role presets.")
    parser.add_argument("--list-profiles", action="store_true", help="Show configured profiles.")
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output.")
    return parser


def _compact_handoff(handoff: dict) -> dict:
    return {
        "owner": handoff.get("owner"),
        "target_app": handoff.get("target_app"),
        "confidence": handoff.get("confidence"),
        "reason": handoff.get("reason"),
        "profile": handoff.get("profile"),
        "role": handoff.get("role"),
        "skills": handoff.get("skills", {}),
        "suggested_safe_commands": handoff.get("safe_commands", []),
        "search_hits": [],
    }


def _compact_evidence(evidence: dict) -> dict:
    handoff = evidence["handoff"]
    return {
        "repo": evidence["repo"],
        "task": evidence["task"],
        "owner": handoff.get("owner"),
        "target_app": handoff.get("target_app"),
        "profile": handoff.get("profile"),
        "role": handoff.get("role"),
        "skills": handoff.get("skills", {}),
        "confidence": handoff.get("confidence"),
        "reason": handoff.get("reason"),
        "missing_guidance": handoff.get("missing_guidance", []),
        "warnings": handoff.get("warnings", []),
        "suggested_safe_commands": handoff.get("safe_commands", []),
        "search_hits": [
            {"term": item["term"], "returncode": item["returncode"],
             "sample": item["output"][:500], "truncated": item["truncated"]}
            for item in evidence.get("searches", [])[:5]
        ],
    }


def _terms_for_task(task: str) -> list[str]:
    text = task.lower()
    config = load_agent_config()
    candidates = []
    for agent in config.get("agents", []):
        candidates.extend(agent.get("keywords", []))
    candidates += ["payment", "redis", "docker", "health", "api", "service", "webhook"]
    selected = []
    for term in candidates:
        if term.lower() in text and term not in selected:
            selected.append(term)
    return selected or ["api", "service"]


def _unavailable_message(provider: str, model: str, info: dict) -> str:
    if provider == "ollama":
        name = model.split(":", 1)[0]
        return (
            f"Ollama unavailable (model: {model})\n"
            f"  brew install ollama && ollama serve && ollama pull {name}\n"
            "  Or: devm config set provider=groq  (free, no local setup needed)"
        )
    env_key = info.get("env_key", "YOUR_API_KEY")
    return (
        f"Provider '{provider}' unavailable (model: {model})\n"
        f"  Set API key: export {env_key}=your-key\n"
        f"  Or:          devm config set api_key=your-key\n"
        "  Or use free local option: devm config set provider=ollama model=llama3.2"
    )


def _print_handoff(handoff: dict) -> None:
    # ── Header box ──────────────────────────────────────────────────────────
    owner = handoff["owner"]
    target = handoff.get("target_app", "?")
    confidence = handoff.get("confidence", "?")
    mode = handoff.get("mode", "?")
    profile = handoff.get("profile", "?")
    reason = handoff.get("reason", "")

    RESET = "\033[0m"
    BOLD  = "\033[1m"
    GREEN = "\033[32m"
    CYAN  = "\033[36m"
    YELLOW = "\033[33m"
    RED   = "\033[31m"
    DIM   = "\033[2m"

    owner_colors = {"backend": CYAN, "frontend": GREEN, "codex": YELLOW}
    owner_color = owner_colors.get(owner, RESET)

    print(f"\n{BOLD}╭─ DevManager {'─' * 46}╮{RESET}")
    print(f"{BOLD}│{RESET}  {owner_color}{BOLD}→ {target}{RESET}  {DIM}({owner} · {confidence} confidence · {mode}){RESET}")
    if reason:
        print(f"{BOLD}│{RESET}  Reason:  {reason}")
    print(f"{BOLD}│{RESET}  Profile: {profile}" + (f"  Role: {handoff['role']}" if handoff.get("role") else ""))
    print(f"{BOLD}╰{'─' * 51}╯{RESET}")

    skills_daily = handoff.get("skills", {}).get("daily", [])
    skills_lib   = handoff.get("skills", {}).get("library", [])
    if skills_daily or skills_lib:
        skill_ids = [s["id"] for s in skills_daily] + [s["id"] for s in skills_lib]
        print(f"\n  {DIM}Skills:{RESET}  {', '.join(skill_ids)}")

    if handoff.get("missing_guidance"):
        print(f"\n  {YELLOW}⚠  Missing guidance files:{RESET}")
        for item in handoff["missing_guidance"]:
            print(f"     - {item}")

    if handoff.get("warnings"):
        print(f"\n  {YELLOW}⚠  Warnings:{RESET}")
        for item in handoff["warnings"]:
            print(f"     - {item}")

    print(f"\n{DIM}{'─' * 52}{RESET}")
    print(handoff["prompt"])
    print(f"{DIM}{'─' * 52}{RESET}")

    if handoff.get("safe_commands"):
        print(f"\n  {DIM}Safe commands available (run with --run-safe):{RESET}")
        for cmd in handoff["safe_commands"]:
            print(f"    {DIM}$ {cmd}{RESET}")

    if handoff.get("safe_command_results"):
        print(f"\n{BOLD}Safe command results:{RESET}")
        for result in handoff["safe_command_results"]:
            rc = result["returncode"]
            if rc == 0:
                status = f"{GREEN}✓ OK{RESET}"
            elif rc == 126:
                status = f"{YELLOW}⊘ BLOCKED{RESET}"
            else:
                status = f"{RED}✗ FAIL({rc}){RESET}"
            print(f"  {status}  {result['command']}")
            if result.get("summary") and rc != 0:
                for line in result["summary"].splitlines()[-5:]:
                    print(f"         {DIM}{line}{RESET}")

    print(f"\n  {GREEN}▶  Prompt ready — open {target} and paste (⌘V){RESET}\n")


def _print_agent_header(agent_key: str, handoff: dict) -> None:
    BOLD = "\033[1m"; DIM = "\033[2m"; GREEN = "\033[32m"; CYAN = "\033[36m"; RESET = "\033[0m"
    agents = discover_agents()
    info = agents.get(agent_key, {})
    owner = handoff.get("owner", "?")
    target = handoff.get("target_app", "?")
    skills = [s["id"] for s in handoff.get("skills", {}).get("daily", [])] + \
             [s["id"] for s in handoff.get("skills", {}).get("library", [])]
    print(f"\n{BOLD}╭─ Agent Run {'─' * 41}╮{RESET}")
    print(f"{BOLD}│{RESET}  {CYAN}{BOLD}{agent_key}{RESET}  {DIM}({info.get('name', agent_key)}){RESET}")
    print(f"{BOLD}│{RESET}  Route: {owner} → {target}  Confidence: {handoff.get('confidence','?')}")
    if skills:
        print(f"{BOLD}│{RESET}  Skills: {', '.join(skills)}")
    print(f"{BOLD}│{RESET}  {DIM}Prompt: {len(handoff.get('prompt',''))} chars — sending to CLI{RESET}")
    print(f"{BOLD}╰{'─' * 52}╯{RESET}\n")


def _print_agents(as_json: bool = False) -> None:
    config = load_agent_config()
    agents = config.get("agents", [])
    if as_json:
        print(json.dumps(agents, indent=2, ensure_ascii=False))
        return
    print("Configured agents:")
    for agent in agents:
        print(f"  {agent['id']}: {agent['name']} → app={agent['app']}")
        if agent.get("description"):
            print(f"    {agent['description']}")


def _print_roles(as_json: bool = False) -> None:
    roles = list_roles()
    if as_json:
        print(json.dumps(roles, indent=2, ensure_ascii=False))
        return
    print("Role presets:")
    for role in roles:
        print(f"  {role['id']}: {role['label']}")


def _print_profiles(as_json: bool = False) -> None:
    profiles = load_profiles_config()
    if as_json:
        print(json.dumps(profiles, indent=2, ensure_ascii=False))
        return
    print(f"Profiles (default={profiles['default']}):")
    for name, profile in profiles.get("profiles", {}).items():
        print(f"  {name}: {profile.get('description')}")


# ─────────────────────────────────────────────────────────────────────────────
# Interactive agent-add wizard
# ─────────────────────────────────────────────────────────────────────────────

def _run_agent_add_wizard() -> int:
    """Fully interactive wizard — discover installed AI CLIs, pick one, register it."""
    import shutil as _shutil
    from pathlib import Path as _Path

    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    GREEN   = "\033[32m"
    CYAN    = "\033[36m"
    YELLOW  = "\033[33m"
    RED     = "\033[31m"

    from .agent_bridge import (
        discover_agents, register_custom_agent,
        _load_custom_agents, _BUILTIN_REGISTRY,
    )

    print(f"\n{BOLD}{CYAN}⚡ Devm — Add an AI Agent{RESET}")
    print(f"  {DIM}Scanning your machine…{RESET}\n")

    # ── Step 1: what's already discovered (all search paths checked) ──────────
    discovered = discover_agents(force=True)   # uses builtin registry + PATH + custom

    # ── Step 2: scan /Applications for AI apps not yet in registry ───────────
    # Map: app bundle name → (key, display_name, cli_candidates)
    _APP_MAP: list[dict] = [
        {"app": "Claude.app",         "key": "claude",     "name": "Claude Code",
         "cli": ["Contents/Resources/claude", "Contents/MacOS/claude"]},
        {"app": "Claude Code.app",    "key": "claude",     "name": "Claude Code",
         "cli": ["Contents/Resources/claude", "Contents/MacOS/claude"]},
        {"app": "Codex.app",          "key": "codex",      "name": "Codex CLI",
         "cli": ["Contents/Resources/codex",  "Contents/MacOS/codex"]},
        {"app": "Cursor.app",         "key": "cursor",     "name": "Cursor",
         "cli": ["Contents/Resources/app/bin/cursor", "Contents/MacOS/Cursor"]},
        {"app": "Windsurf.app",       "key": "windsurf",   "name": "Windsurf",
         "cli": ["Contents/Resources/app/bin/windsurf", "Contents/MacOS/Windsurf"]},
        {"app": "Zed.app",            "key": "zed",        "name": "Zed",
         "cli": ["Contents/MacOS/cli"]},
        {"app": "Warp.app",           "key": "warp",       "name": "Warp",
         "cli": ["Contents/MacOS/warp-cli"]},
        {"app": "Cody.app",           "key": "cody",       "name": "Cody (Sourcegraph)",
         "cli": ["Contents/Resources/cody", "Contents/MacOS/cody"]},
        {"app": "Goose.app",          "key": "goose",      "name": "Goose",
         "cli": ["Contents/Resources/goose", "Contents/MacOS/goose"]},
        {"app": "Amp.app",            "key": "amp",        "name": "Sourcegraph Amp",
         "cli": ["Contents/Resources/amp",   "Contents/MacOS/amp"]},
        # Antigravity is a GUI-only IDE — no CLI interface, skip it
        {"app": "GitHub Desktop.app", "key": "gh",         "name": "GitHub Copilot CLI",
         "cli": []},
    ]

    # PATH-based extras (not in /Applications)
    _PATH_EXTRAS = [
        ("aider",      "Aider",           "pip install aider-chat"),
        ("gemini",     "Gemini CLI",      "npm i -g @google/gemini-cli"),
        ("opencode",   "OpenCode",        "github.com/sst/opencode"),
        ("gh",         "GitHub CLI",      "brew install gh"),
        ("ollama",     "Ollama",          "ollama.ai"),
        ("continue",   "Continue",        "continue.dev"),
        ("tabnine",    "Tabnine",         "tabnine.com"),
        ("phind",      "Phind",           "phind.com"),
        ("supermaven", "Supermaven",      "supermaven.com"),
        ("amp",        "Amp",             "ampcode.com"),
    ]

    extra_found: dict[str, dict] = {}  # key → {name, path}

    # Scan /Applications
    for app_info in _APP_MAP:
        app_path = _Path("/Applications") / app_info["app"]
        if not app_path.exists():
            continue
        key = app_info["key"]
        if key in discovered or key in extra_found:
            continue
        for rel in app_info["cli"]:
            candidate = app_path / rel
            if candidate.exists() and os.access(str(candidate), os.X_OK):
                extra_found[key] = {"name": app_info["name"], "path": str(candidate)}
                break

    # Scan PATH for extras
    for bin_name, display, _hint in _PATH_EXTRAS:
        if bin_name in discovered or bin_name in extra_found:
            continue
        p = _shutil.which(bin_name)
        if p:
            extra_found[bin_name] = {"name": display, "path": p}

    # ── Display: already in Devm ──────────────────────────────────────────────
    if discovered:
        print(f"  {GREEN}Already in Devm:{RESET}")
        for key, info in discovered.items():
            strengths = ", ".join(info.get("strengths", []))
            binary_path = info.get("binary", "")
            # Shorten long paths
            try:
                short = str(_Path(binary_path).relative_to(_Path.home()))
                short = "~/" + short
            except ValueError:
                short = binary_path
            print(f"    {GREEN}✓{RESET}  {BOLD}{info.get('name', key):<22}{RESET}  "
                  f"{DIM}{short}{RESET}")
        print()

    # ── Display: found but not in Devm ───────────────────────────────────────
    new_agents = [
        {"key": k, "name": v["name"], "path": v["path"]}
        for k, v in extra_found.items()
    ]

    if not new_agents:
        print(f"  {GREEN}All found AI CLIs are already in Devm.{RESET}")
        print(f"\n  {DIM}Don't see an agent? Try:{RESET}")
        print(f"  {DIM}  · Install it, then run 'devm agent-add' again{RESET}")
        print(f"  {DIM}  · Pick option below to add any custom binary{RESET}\n")
        # Still offer custom add
        new_agents = []

    else:
        print(f"  {BOLD}Found {len(new_agents)} more AI CLI(s) on your machine:{RESET}\n")
        for i, a in enumerate(new_agents, 1):
            try:
                short = str(_Path(a["path"]).relative_to(_Path.home()))
                short = "~/" + short
            except ValueError:
                short = a["path"]
            print(f"    {CYAN}{i}{RESET}.  {BOLD}{a['name']:<22}{RESET}  {DIM}{short}{RESET}")
        print()

    # Custom binary option always at end
    custom_num = len(new_agents) + 1
    print(f"    {CYAN}{custom_num}{RESET}.  {DIM}Add custom binary (enter path manually){RESET}")
    print(f"\n    {DIM}Enter number(s), e.g.  1  or  1 2  or  all   (Enter to cancel){RESET}\n")

    # ── Input ─────────────────────────────────────────────────────────────────
    try:
        sys.stdout.write(f"  {CYAN}Select>{RESET} ")
        sys.stdout.flush()
        answer = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        print(f"\n  {DIM}Cancelled.{RESET}\n")
        return 0

    if not answer:
        print(f"  {DIM}Nothing added.{RESET}\n")
        return 0

    # Parse selection
    to_add: list[dict] = []
    if answer in ("all", "a") and new_agents:
        to_add = new_agents[:]
    else:
        for part in answer.split():
            try:
                idx = int(part)
            except ValueError:
                continue
            if 1 <= idx <= len(new_agents):
                to_add.append(new_agents[idx - 1])
            elif idx == custom_num:
                to_add.append({"key": "__custom__"})

    if not to_add:
        print(f"  {DIM}Nothing selected.{RESET}\n")
        return 0

    print()

    # ── Register ──────────────────────────────────────────────────────────────
    added = 0
    for agent in to_add:
        if agent["key"] == "__custom__":
            _wizard_add_custom()
            added += 1
            continue

        # Inherit stdin/cmd_template from builtin registry if known
        builtin = _BUILTIN_REGISTRY.get(agent["key"], {})
        register_custom_agent(
            key=agent["key"],
            binary=agent["path"],
            name=agent["name"],
            stdin=builtin.get("stdin", True),
            strengths=builtin.get("strengths", ["general"]),
        )
        print(f"  {GREEN}✓  Added:{RESET} {BOLD}{agent['name']}{RESET}  {DIM}({agent['path']}){RESET}")
        added += 1

    print(f"\n  {GREEN}{BOLD}{added} agent(s) added.{RESET}  Run {CYAN}devm agents{RESET} to verify.\n")
    return 0


def _wizard_add_custom() -> None:
    """Sub-wizard to register any custom binary."""
    RESET = "\033[0m"; BOLD = "\033[1m"; DIM = "\033[2m"
    GREEN = "\033[32m"; CYAN = "\033[36m"; RED = "\033[31m"

    print(f"\n  {BOLD}Custom agent setup{RESET}")

    def _ask(prompt: str, default: str = "") -> str:
        hint = f" [{default}]" if default else ""
        sys.stdout.write(f"  {CYAN}{prompt}{hint}>{RESET} ")
        sys.stdout.flush()
        try:
            val = input().strip()
        except (EOFError, KeyboardInterrupt):
            return default
        return val or default

    binary = _ask("Path to binary (e.g. /usr/local/bin/mytool)")
    if not binary:
        print(f"  {DIM}Skipped.{RESET}")
        return

    import shutil as _shutil
    resolved = _shutil.which(binary) or binary
    if not Path(resolved).exists():
        print(f"  {RED}✗  Binary not found: {resolved}{RESET}")
        return

    key = _ask("Short key (used in 'devm --agent KEY')", Path(resolved).name)
    name = _ask("Display name", key.capitalize())
    stdin_str = _ask("Does it accept prompt via stdin? [y/N]", "n")
    stdin = stdin_str.lower() in ("y", "yes")

    from .agent_bridge import register_custom_agent
    register_custom_agent(key=key, binary=resolved, name=name, stdin=stdin)
    print(f"\n  {GREEN}✓  Added:{RESET} {BOLD}{name}{RESET}  ({resolved})")
