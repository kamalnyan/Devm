"""Background worker — spawned by `devm --bg`.

Usage (internal): python -m devmanager._bg_worker <job_id> <repo> <task> [--profile X] [--role Y]

Runs the full pipeline (context → route → handoff → solve) and writes
the result to ~/.devmanager/jobs/<job_id>.json.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path


def _main() -> None:
    args = sys.argv[1:]
    if len(args) < 3:
        print("Usage: _bg_worker <job_id> <repo> <task> [flags]", file=sys.stderr)
        sys.exit(1)

    job_id = args[0]
    repo = args[1]
    task = args[2]
    extra = args[3:]

    # Parse optional flags from extra
    profile = _flag(extra, "--profile")
    role = _flag(extra, "--role")
    provider = _flag(extra, "--provider")
    model = _flag(extra, "--model")
    api_key = _flag(extra, "--api-key")
    agent_key = _flag(extra, "--agent")
    use_a2a = "--a2a" in extra
    use_council = "--council" in extra
    use_adk_council = "--adk-council" in extra
    no_llm = "--no-llm" in extra

    from devmanager.jobs import update_job
    from devmanager.context import collect_context
    from devmanager.router import classify_task
    from devmanager.llm import ask as ask_llm
    from devmanager.handoff import build_handoff
    from devmanager.solver import solve
    from devmanager.agent_bridge import run_agent, agent_for_owner as bridge_agent_for_owner
    from devmanager.council import Council, run_adk_council
    from devmanager.user_config import load as load_cfg

    update_job(job_id, status="running", started_at=datetime.now().isoformat(timespec="seconds"))

    try:
        repo_path = Path(repo).expanduser().resolve()
        context = collect_context(repo_path)
        local_route = classify_task(task, context)

        # Build effective LLM config
        cfg = load_cfg()
        if provider:
            cfg["provider"] = provider
        if model:
            cfg["model"] = model
        if api_key:
            cfg["api_key"] = api_key

        llm_route = None
        if not no_llm:
            llm_route = ask_llm(task=task, context=context, local_route=local_route, cfg=cfg)

        route = llm_route or local_route
        handoff = build_handoff(
            task=task,
            route=route,
            context=context,
            used_llm=bool(llm_route),
            profile=profile,
            role=role,
        )

        # Solve: a2a → adk-council → council → agent → LLM solver
        result_text = ""
        if use_a2a:
            from devmanager.a2a import A2ACouncil
            a2a = A2ACouncil(task=task, repo=str(repo_path),
                             handoff_prompt=handoff["prompt"], cfg=cfg, interactive=False)
            session = a2a.run()
            result_text = session.chat_log()
        if use_adk_council and not result_text:
            import asyncio
            try:
                result_text = asyncio.run(
                    run_adk_council(task, str(repo_path), handoff["prompt"], cfg=cfg)
                )
            except Exception:
                use_council = True  # fallback
        if use_council and not result_text:
            council = Council(task=task, repo=str(repo_path),
                              handoff_prompt=handoff["prompt"], stream=False, cfg=cfg)
            session = council.run()
            result_text = session.transcript()
        if not result_text and agent_key:
            actual_key = agent_key
            if actual_key == "auto":
                actual_key = bridge_agent_for_owner(handoff["owner"]) or "claude"
            print(f"[bg_worker] Using agent CLI: {actual_key}")
            agent_result = run_agent(actual_key, handoff["prompt"], repo=repo_path, stream=False)
            result_text = agent_result.get("output", "")
            if not agent_result["ok"] and not result_text:
                raise RuntimeError(f"Agent '{actual_key}' failed: {agent_result.get('error','unknown')}")
        else:
            result_text = solve(handoff["prompt"], cfg=cfg, stream=False)
            if not result_text:
                raise RuntimeError("LLM returned empty response. Check provider config: devm config")

        update_job(
            job_id,
            status="done",
            finished_at=datetime.now().isoformat(timespec="seconds"),
            result=result_text,
            route={
                "owner": handoff.get("owner"),
                "target_app": handoff.get("target_app"),
                "confidence": handoff.get("confidence"),
                "mode": handoff.get("mode"),
                "reason": handoff.get("reason"),
            },
            skills=handoff.get("skills", {}),
        )
        print(f"[bg_worker] Job {job_id} done.")

    except Exception as exc:  # noqa: BLE001
        import traceback
        update_job(
            job_id,
            status="failed",
            finished_at=datetime.now().isoformat(timespec="seconds"),
            error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-800:]}",
        )
        print(f"[bg_worker] Job {job_id} FAILED: {exc}", file=sys.stderr)
        sys.exit(1)


def _flag(args: list[str], name: str) -> str | None:
    for i, a in enumerate(args):
        if a == name and i + 1 < len(args):
            return args[i + 1]
    return None


if __name__ == "__main__":
    _main()
