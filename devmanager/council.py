"""Multi-agent Council — ALL available agents collaborate to solve a task.

Pipeline (dynamic — uses every authenticated agent found):
  1. Planner      (Ollama/GLM4 — free local) — breaks task into subtasks
  2. Explorer(s)  (all available agents, different ones if possible) — read code
  3. Analyst(s)   (all available agents) — deep analysis + fix proposal
  4. Reviewer(s)  (different agents than analysts) — independent review
  5. Synthesizer  (Ollama/GLM4) — merges everything into final answer

If only one CLI agent is available, it plays all roles sequentially.
If multiple agents available, they're assigned different roles for independent perspectives.
The more agents installed, the richer the council.
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .agent_bridge import run_agent, discover_agents, check_agent_auth, available_agents, agents_for_role
from .solver import solve as ollama_solve
from .user_config import load as load_cfg


# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AgentMessage:
    role: str           # planner / explorer / analyst / reviewer / synthesizer
    agent: str          # claude / codex / ollama / local
    prompt: str
    response: str = ""
    elapsed: float = 0.0
    ok: bool = True
    error: str = ""


@dataclass
class CouncilSession:
    task: str
    repo: str
    handoff_prompt: str
    messages: list[AgentMessage] = field(default_factory=list)
    final_answer: str = ""
    total_elapsed: float = 0.0

    def transcript(self) -> str:
        parts = [f"# Council Session\n\nTask: {self.task}\n"]
        for msg in self.messages:
            parts.append(f"\n## [{msg.role.upper()}] via {msg.agent} ({msg.elapsed:.1f}s)")
            parts.append(msg.response or "(no response)")
        parts.append(f"\n## FINAL ANSWER\n{self.final_answer}")
        return "\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Prompts for each agent role
# ─────────────────────────────────────────────────────────────────────────────

def _planner_prompt(task: str, handoff: str) -> str:
    return f"""You are a dev task planner. Break this task into 2-3 concrete subtasks.
Be specific. Each subtask should be independently actionable.
Output ONLY a numbered list of subtasks, nothing else.

Task: {task}

Repo context (brief):
{handoff[:800]}
"""


def _explorer_prompt(task: str, subtasks: str, handoff: str) -> str:
    return f"""You are a code explorer agent. Your ONLY job is to READ code and report findings.
Do NOT suggest fixes. Do NOT write code. Just explore and report.

Original task: {task}

Subtasks to explore for:
{subtasks}

Full repo brief:
{handoff}

Your output:
1. List exact file paths relevant to each subtask
2. Quote the key code sections (function names, class names, line hints)
3. Identify the root cause or gap for each subtask
4. Note any contracts between modules (API shapes, types, interfaces)

Be specific. Cite real files and symbols from the repo context above.
"""


def _analyst_prompt(task: str, subtasks: str, explorer_output: str, handoff: str) -> str:
    return f"""You are an expert software engineer. The explorer agent has already found the relevant code.
Your job: produce a concrete fix or implementation plan.

Original task: {task}

Subtasks:
{subtasks}

Explorer findings:
{explorer_output}

Full repo context:
{handoff[:3000]}

Your output:
1. Root cause analysis (1-2 sentences per subtask)
2. Exact code changes needed — show the diff or before/after snippet
3. Files to touch (be specific: filename + function/method)
4. Any risks or side effects to watch for
5. Verification commands to run after the fix

Write production-ready code. Follow the patterns already in the codebase.
"""


def _reviewer_prompt(task: str, analyst_output: str, explorer_output: str) -> str:
    return f"""You are a strict code reviewer. Another agent has suggested a fix. Your job: find problems.

Original task: {task}

Explorer findings:
{explorer_output[:1500]}

Analyst's proposed fix:
{analyst_output}

Review checklist:
- Is the root cause diagnosis correct?
- Are there edge cases the fix misses?
- Will this break any existing tests or contracts?
- Are there security implications?
- Is the fix minimal (no unnecessary changes)?
- Anything missing from the verification steps?

Output:
1. VERDICT: APPROVE / REQUEST_CHANGES / NEEDS_DISCUSSION
2. Issues found (if any) — be specific, cite line/function
3. Suggested improvements
4. Final recommendation
"""


def _synthesizer_prompt(task: str, messages: list[AgentMessage]) -> str:
    transcript = "\n\n".join(
        f"=== {m.role.upper()} ({m.agent}) ===\n{m.response}"
        for m in messages if m.response
    )
    return f"""You are a synthesis agent. Multiple AI agents have worked on this task.
Combine their work into a single, clear, actionable final answer.

Original task: {task}

Agent outputs:
{transcript}

Your final answer should:
1. State the root cause clearly
2. Show the exact fix (code + files)
3. Include verification steps
4. Note any open questions or risks

Be concise. Remove redundancy across agents. Prioritize the analyst's code + reviewer's corrections.
"""


# ─────────────────────────────────────────────────────────────────────────────
# Council runner
# ─────────────────────────────────────────────────────────────────────────────

class Council:
    """Orchestrates multiple AI agents to collaboratively solve a dev task."""

    BOLD  = "\033[1m"
    DIM   = "\033[2m"
    CYAN  = "\033[36m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED   = "\033[31m"
    RESET = "\033[0m"

    def __init__(
        self,
        task: str,
        repo: str,
        handoff_prompt: str,
        stream: bool = True,
        cfg: dict[str, Any] | None = None,
    ) -> None:
        self.task = task
        self.repo = repo
        self.handoff_prompt = handoff_prompt
        self.stream = stream
        self.cfg = cfg or load_cfg()
        self.all_agents = discover_agents()
        self.avail = available_agents()  # authenticated subset
        self.session = CouncilSession(task=task, repo=repo, handoff_prompt=handoff_prompt)

    # ── Public entry point ────────────────────────────────────────────────

    def run(self) -> CouncilSession:
        t0 = time.time()
        self._print_header()

        # 1. Plan (Ollama local — always free)
        subtasks = self._step_plan()

        # 2. Explore — one or more agents read the repo
        explorer_out = self._step_explore(subtasks)

        # 3. Analyse — different agent(s) write the fix
        analyst_out = self._step_analyse(subtasks, explorer_out)

        # 4. Review — agents that DIDN'T do analyst review independently
        reviewer_out = self._step_review(analyst_out, explorer_out)

        # 5. Extra perspectives — any remaining agents add their take
        self._step_extra_perspectives(subtasks, analyst_out, explorer_out)

        # 6. Synthesize (Ollama — merges everything)
        final = self._step_synthesize()

        self.session.final_answer = final
        self.session.total_elapsed = time.time() - t0

        self._print_footer()
        return self.session

    # ── Steps ─────────────────────────────────────────────────────────────

    def _step_plan(self) -> str:
        self._print_step("PLANNER", "ollama/glm4", "Breaking task into subtasks…")
        prompt = _planner_prompt(self.task, self.handoff_prompt)
        t0 = time.time()
        response = ollama_solve(prompt, cfg=self.cfg, stream=False)
        elapsed = time.time() - t0

        if not response:
            response = f"1. Investigate {self.task}\n2. Identify root cause\n3. Suggest fix"

        msg = AgentMessage("planner", "ollama", prompt, response, elapsed, bool(response))
        self.session.messages.append(msg)
        self._print_agent_output("PLANNER", response, elapsed)
        return response

    def _step_explore(self, subtasks: str) -> str:
        # Use ALL available agents suited for exploration, run sequentially
        explorers = agents_for_role("explorer")
        if not explorers:
            explorers = list(self.avail.keys())

        combined_out: list[str] = []
        for agent_key in explorers:
            self._print_step("EXPLORER", agent_key, "Reading repo code…")
            prompt = _explorer_prompt(self.task, subtasks, self.handoff_prompt)
            t0 = time.time()
            result = run_agent(agent_key, prompt, repo=self.repo, stream=self.stream)
            elapsed = time.time() - t0
            response = result["output"] or result.get("error", "")
            msg = AgentMessage("explorer", agent_key, prompt, response, elapsed, result["ok"], result.get("error", ""))
            self.session.messages.append(msg)
            if not self.stream:
                self._print_agent_output(f"EXPLORER/{agent_key}", response, elapsed)
            if response:
                combined_out.append(f"=== {agent_key} exploration ===\n{response}")

        if not combined_out:
            # Fallback to Ollama if no CLI agents available
            self._print_step("EXPLORER", "ollama", "Reading repo context…")
            prompt = _explorer_prompt(self.task, subtasks, self.handoff_prompt)
            t0 = time.time()
            response = ollama_solve(prompt, cfg=self.cfg, stream=self.stream)
            elapsed = time.time() - t0
            msg = AgentMessage("explorer", "ollama", prompt, response or "", elapsed, bool(response))
            self.session.messages.append(msg)
            return response or ""

        return "\n\n".join(combined_out)

    def _step_analyse(self, subtasks: str, explorer_out: str) -> str:
        # Use ALL available agents for analysis — each adds their perspective
        analysts = agents_for_role("analyst")
        if not analysts:
            analysts = list(self.avail.keys())

        combined_out: list[str] = []
        for agent_key in analysts:
            self._print_step("ANALYST", agent_key, "Analysing code + writing fix…")
            prompt = _analyst_prompt(self.task, subtasks, explorer_out, self.handoff_prompt)
            t0 = time.time()
            result = run_agent(agent_key, prompt, repo=self.repo, stream=self.stream)
            elapsed = time.time() - t0
            response = result["output"] or result.get("error", "")
            msg = AgentMessage("analyst", agent_key, prompt, response, elapsed, result["ok"], result.get("error", ""))
            self.session.messages.append(msg)
            if not self.stream:
                self._print_agent_output(f"ANALYST/{agent_key}", response, elapsed)
            if response:
                combined_out.append(f"=== {agent_key} analysis ===\n{response}")

        if not combined_out:
            self._print_step("ANALYST", "ollama", "Analysing with local model…")
            prompt = _analyst_prompt(self.task, subtasks, explorer_out, self.handoff_prompt)
            t0 = time.time()
            response = ollama_solve(prompt, cfg=self.cfg, stream=self.stream)
            elapsed = time.time() - t0
            msg = AgentMessage("analyst", "ollama", prompt, response or "", elapsed, bool(response))
            self.session.messages.append(msg)
            return response or ""

        return "\n\n".join(combined_out)

    def _step_review(self, analyst_out: str, explorer_out: str) -> str:
        # Reviewers: agents that DIDN'T do analysis, OR all agents if only one available
        analyst_agents = {m.agent for m in self.session.messages if m.role == "analyst"}
        reviewers = agents_for_role("reviewer")
        # Prefer agents that didn't analyze — for independent perspective
        independent = [a for a in reviewers if a not in analyst_agents]
        review_pool = independent if independent else reviewers

        if not review_pool:
            review_pool = list(self.avail.keys()) or []

        combined_out: list[str] = []
        for agent_key in review_pool:
            self._print_step("REVIEWER", agent_key, "Reviewing proposed fix…")
            prompt = _reviewer_prompt(self.task, analyst_out, explorer_out)
            t0 = time.time()
            result = run_agent(agent_key, prompt, repo=self.repo, stream=self.stream)
            elapsed = time.time() - t0
            response = result["output"] or result.get("error", "")
            msg = AgentMessage("reviewer", agent_key, prompt, response, elapsed, result["ok"], result.get("error", ""))
            self.session.messages.append(msg)
            if not self.stream:
                self._print_agent_output(f"REVIEWER/{agent_key}", response, elapsed)
            if response:
                combined_out.append(f"=== {agent_key} review ===\n{response}")

        if not combined_out:
            self._print_step("REVIEWER", "ollama", "Reviewing with local model…")
            prompt = _reviewer_prompt(self.task, analyst_out, explorer_out)
            t0 = time.time()
            response = ollama_solve(prompt, cfg=self.cfg, stream=self.stream)
            elapsed = time.time() - t0
            msg = AgentMessage("reviewer", "ollama", prompt, response or "", elapsed, bool(response))
            self.session.messages.append(msg)
            return response or ""

        return "\n\n".join(combined_out)

    def _step_extra_perspectives(self, subtasks: str, analyst_out: str, explorer_out: str) -> None:
        """Give any remaining agents (not yet used) a chance to add their perspective."""
        used = {m.agent for m in self.session.messages}
        remaining = [k for k in self.avail if k not in used]
        if not remaining:
            return

        for agent_key in remaining:
            self._print_step("CONTRIBUTOR", agent_key, "Adding independent perspective…")
            prompt = (
                f"You are an additional expert reviewing this dev task.\n\n"
                f"Task: {self.task}\n\n"
                f"Explorer found:\n{explorer_out[:1000]}\n\n"
                f"Analyst proposed:\n{analyst_out[:1500]}\n\n"
                f"Add your perspective: What did the others miss? "
                f"Any alternative approach? Any additional risks?"
            )
            t0 = time.time()
            result = run_agent(agent_key, prompt, repo=self.repo, stream=self.stream)
            elapsed = time.time() - t0
            response = result["output"] or ""
            if response:
                msg = AgentMessage("contributor", agent_key, prompt, response, elapsed, result["ok"])
                self.session.messages.append(msg)
                if not self.stream:
                    self._print_agent_output(f"CONTRIBUTOR/{agent_key}", response, elapsed)

    def _step_synthesize(self) -> str:
        self._print_step("SYNTHESIZER", "ollama/glm4", "Merging all agent outputs…")
        prompt = _synthesizer_prompt(self.task, self.session.messages)
        t0 = time.time()
        response = ollama_solve(prompt, cfg=self.cfg, stream=self.stream)
        elapsed = time.time() - t0

        if not response:
            # Fallback: concatenate key outputs
            response = "\n\n---\n\n".join(
                f"**{m.role.upper()}**:\n{m.response}"
                for m in self.session.messages if m.response
            )

        msg = AgentMessage("synthesizer", "ollama", prompt, response, elapsed, bool(response))
        self.session.messages.append(msg)
        if not self.stream:
            self._print_agent_output("SYNTHESIZER", response, elapsed)
        return response

    # ── Print helpers ──────────────────────────────────────────────────────

    def _print_header(self) -> None:
        agents_str = " + ".join(self.avail.keys()) or "ollama only"
        print(f"\n{self.BOLD}╭─ Council Session {'─' * 35}╮{self.RESET}")
        print(f"{self.BOLD}│{self.RESET}  Task:   {self.task[:60]}")
        print(f"{self.BOLD}│{self.RESET}  Agents: {self.CYAN}{agents_str}{self.RESET}")
        print(f"{self.BOLD}│{self.RESET}  Pipeline: Planner → Explorer → Analyst → Reviewer → Synthesizer")
        print(f"{self.BOLD}╰{'─' * 52}╯{self.RESET}\n")

    def _print_step(self, role: str, agent: str, desc: str) -> None:
        role_colors = {
            "PLANNER": self.DIM,
            "EXPLORER": self.CYAN,
            "ANALYST": self.GREEN,
            "REVIEWER": self.YELLOW,
            "SYNTHESIZER": self.BOLD,
        }
        color = role_colors.get(role, "")
        print(f"\n{color}{'─' * 52}{self.RESET}")
        print(f"{color}▶  {role}{self.RESET}  {self.DIM}via {agent}{self.RESET}  — {desc}")
        print(f"{self.DIM}{'─' * 52}{self.RESET}\n")

    def _print_agent_output(self, role: str, response: str, elapsed: float) -> None:
        print(response)
        print(f"\n{self.DIM}[{role} · {elapsed:.1f}s]{self.RESET}")

    def _print_footer(self) -> None:
        t = self.session.total_elapsed
        roles = [m.role for m in self.session.messages]
        print(f"\n{self.BOLD}{'═' * 52}{self.RESET}")
        print(f"{self.BOLD}Council complete{self.RESET}  {self.DIM}{t:.1f}s total · {len(roles)} agents{self.RESET}")
        print(f"{self.BOLD}{'═' * 52}{self.RESET}\n")


# ─────────────────────────────────────────────────────────────────────────────
# ADK integration — Council as an ADK orchestrator
# ─────────────────────────────────────────────────────────────────────────────

def build_adk_council_agent(cfg: dict[str, Any] | None = None):
    """Build a Google ADK agent whose tools ARE all discovered CLI agents.

    Dynamically creates a tool for EVERY available agent — no hardcoding.
    The ADK LLM (Ollama/GLM4) decides which tools to call and in what order.
    """
    try:
        from google.adk.agents import Agent
        from google.adk.tools import FunctionTool
    except ImportError:
        return None

    _cfg = cfg or load_cfg()
    avail = available_agents()
    tools: list = []
    agent_descriptions: list[str] = []

    # Dynamically create a tool function for each available agent
    for agent_key, info in avail.items():
        name = info.get("name", agent_key)
        strengths = ", ".join(info.get("strengths", ["general"]))

        # Capture agent_key in closure
        def _make_tool(key: str, aname: str):
            def call_agent(prompt: str) -> str:
                f"""Call {aname} agent with the given prompt."""
                result = run_agent(key, prompt, stream=False)
                return result["output"] or result.get("error", "No output.")
            call_agent.__name__ = f"call_{key.replace('-', '_')}"
            call_agent.__doc__ = (
                f"Call {aname} ({key}). "
                f"Strengths: {strengths}. "
                "Use this for tasks this agent excels at."
            )
            return call_agent

        tool_fn = _make_tool(agent_key, name)
        tools.append(FunctionTool(tool_fn))
        agent_descriptions.append(f"- call_{agent_key.replace('-','_')}: {name} — strengths: {strengths}")

    def call_ollama(prompt: str) -> str:
        """Call local Ollama/GLM4 for planning, summarization, or fallback."""
        return ollama_solve(prompt, cfg=_cfg, stream=False) or "No response from Ollama."

    def read_file(file_path: str) -> str:
        """Read a file from the repo. Always read before suggesting changes to it."""
        try:
            p = Path(file_path).expanduser()
            if not p.exists():
                return f"File not found: {file_path}"
            content = p.read_text(encoding="utf-8", errors="replace")
            return content[:4000] + "\n...(truncated)" if len(content) > 4000 else content
        except Exception as exc:
            return f"Error: {exc}"

    tools += [FunctionTool(call_ollama), FunctionTool(read_file)]
    agent_list_str = "\n".join(agent_descriptions) or "- call_ollama: local GLM4"

    instruction = f"""You are an AI dev manager orchestrating a team of specialist AI agents.

Available agents (call them as tools):
{agent_list_str}
- call_ollama: Local GLM4 — for planning, synthesis, fallback.
- read_file: Read any file from the repo (use BEFORE suggesting changes).

Your strategy:
1. EXPLORE — call agents suited for exploration to find relevant files and understand the code.
2. IMPLEMENT — call agents suited for implementation to write the fix.
3. REVIEW — call a DIFFERENT agent to independently review the fix.
4. SYNTHESIZE — combine all findings into a clean final answer.

Rules:
- Always pass previous agents' outputs to the next agent as context.
- Use read_file before asking agents to change a file.
- Use as many agents as needed — more perspectives = better result.
- Always include file paths and code snippets in your prompts to agents.
"""

    return Agent(
        name="council_orchestrator",
        model=f"ollama/{_cfg.get('model', 'glm4:latest')}",
        instruction=instruction,
        tools=tools,
    )


async def run_adk_council(
    task: str,
    repo: str,
    handoff_prompt: str,
    cfg: dict[str, Any] | None = None,
) -> str:
    """Run the ADK-based council where GLM4 orchestrates Claude + Codex."""
    try:
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from google.genai import types
    except ImportError:
        raise RuntimeError(
            "google-adk not installed.\n"
            "Run: pip install google-adk\n"
            "Or use: devm --council (non-ADK mode)"
        )

    _cfg = cfg or load_cfg()
    agent = build_adk_council_agent(_cfg)
    if not agent:
        raise RuntimeError("Failed to build ADK council agent.")

    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name="devmanager_council", user_id="local")
    runner = Runner(
        app_name="devmanager_council",
        agent=agent,
        session_service=session_service,
    )

    full_prompt = (
        f"Repo: {repo}\n\n"
        f"Task: {task}\n\n"
        f"Repo context:\n{handoff_prompt[:4000]}\n\n"
        "Solve this task using your agent tools. "
        "Start with call_codex to explore, then call_claude to implement, "
        "then call_codex to review. Give a final unified answer."
    )

    message = types.Content(role="user", parts=[types.Part.from_text(text=full_prompt)])
    collected: list[str] = []

    print("\n" + "═" * 52)
    print("ADK Council — GLM4 orchestrating Claude + Codex")
    print("═" * 52 + "\n")

    async for event in runner.run_async(
        user_id="local", session_id=session.id, new_message=message
    ):
        content = getattr(event, "content", None)
        if not content or not content.parts:
            continue
        for part in content.parts:
            text = getattr(part, "text", None)
            if text:
                print(text, end="", flush=True)
                collected.append(text)

    print()
    return "".join(collected)
