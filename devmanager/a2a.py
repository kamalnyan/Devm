"""Agent-to-Agent (A2A) Communication — real bidirectional messaging between AI agents.

Agents actually talk to each other:

  codex   → "Found webhook handler at src/payments/webhook.ts:47"
  claude  → "@codex: What's the signature header name used there?"
  codex   → "It's X-Razorpay-Signature, validated on line 52"
  claude  → "@codex: Is there a timeout guard? I don't see one."
  codex   → "No timeout guard — that's the bug."
  claude  → "Fix: add crypto.timingSafeEqual + 30s timeout..."
  reviewer→ "@claude: Did you handle the case where header is missing?"
  claude  → "Good catch — adding null guard."
  synth   → "Final answer combining all findings..."

Protocol:
  - Agents write @agent_name: message to address a specific agent
  - Orchestrator intercepts these, routes them, feeds replies back
  - Each agent can ask multiple questions per turn
  - Max rounds prevent infinite loops
  - All messages shown live in terminal as a chat feed
"""
from __future__ import annotations

import re
import sys
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .agent_bridge import available_agents, agents_for_role, _build_command  # type: ignore[attr-defined]
from .interactive import (
    C, ROLE_STYLE, AGENT_COLOR, Spinner, _role_header, _sep,
    _term_width, _elapsed_badge, _SUPPRESS, detect_actions, ask_permission, run_approved_command,
)
from .solver import solve as ollama_solve
from .user_config import load as load_cfg


# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Message:
    from_agent: str         # who sent this
    to_agent: str           # "all" | agent name
    content: str            # message text
    role: str               # planner / explorer / analyst / reviewer / synthesizer
    timestamp: float = field(default_factory=time.time)
    is_question: bool = False
    in_reply_to: str = ""   # agent being replied to


@dataclass
class A2ASession:
    task: str
    repo: str
    messages: list[Message] = field(default_factory=list)
    final: str = ""
    total_elapsed: float = 0.0

    def chat_log(self) -> str:
        lines = [f"# A2A Council\nTask: {self.task}\n"]
        for m in self.messages:
            to = f"→ @{m.to_agent}" if m.to_agent != "all" else "→ all"
            lines.append(f"[{m.from_agent}] {to}: {m.content[:200]}")
        lines.append(f"\n## Final\n{self.final}")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# A2A Protocol parser
# ─────────────────────────────────────────────────────────────────────────────

# Match: @agent_name: text  OR  @agent_name, text  (at line start or mid-text)
_MENTION_RE = re.compile(
    r"@([\w\-]+)\s*[:\,]\s*(.+?)(?=\n@|\n\n|$)", re.S | re.I
)

def parse_mentions(text: str, known_agents: set[str]) -> list[tuple[str, str]]:
    """Extract (@agent, question) pairs from agent output."""
    mentions: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in _MENTION_RE.finditer(text):
        target = m.group(1).lower()
        question = m.group(2).strip()
        if target in known_agents and question and question not in seen:
            seen.add(question)
            mentions.append((target, question))
    return mentions


# ─────────────────────────────────────────────────────────────────────────────
# Terminal Chat UI
# ─────────────────────────────────────────────────────────────────────────────

# Role → compact icon for chat bubbles
_CHAT_ICON = {
    "planner":     "🧠",
    "explorer":    "🔍",
    "analyst":     "⚙️ ",
    "reviewer":    "🔎",
    "synthesizer": "✨",
    "contributor": "💡",
}

def _chat_print(msg: Message, is_new: bool = True) -> None:
    """Print one message in chat-bubble style."""
    acolor = AGENT_COLOR.get(msg.from_agent, C["white"])
    icon = _CHAT_ICON.get(msg.role, "·")
    w = min(_term_width(), 88)

    # Header line: icon agent → @target
    if msg.to_agent == "all":
        target_str = ""
    else:
        tcolor = AGENT_COLOR.get(msg.to_agent, C["white"])
        target_str = f"  {C['dim']}→{C['reset']}  {tcolor}@{msg.to_agent}{C['reset']}"

    marker = f"{C['yellow']}?{C['reset']}  " if msg.is_question else "   "
    print(f"\n  {marker}{icon} {acolor}{C['bold']}{msg.from_agent}{C['reset']}{target_str}")

    # Message body — indented, word-wrapped at terminal width
    indent = "      "
    max_line = w - len(indent) - 2
    for para in msg.content.split("\n"):
        if not para.strip():
            print()
            continue
        # Simple word wrap
        words = para.split()
        line_buf: list[str] = []
        line_len = 0
        for word in words:
            if line_len + len(word) + 1 > max_line and line_buf:
                print(f"{indent}{C['dim'] if msg.is_question else ''}{' '.join(line_buf)}{C['reset'] if msg.is_question else ''}")
                line_buf = [word]
                line_len = len(word)
            else:
                line_buf.append(word)
                line_len += len(word) + 1
        if line_buf:
            print(f"{indent}{C['dim'] if msg.is_question else ''}{' '.join(line_buf)}{C['reset'] if msg.is_question else ''}")


def _chat_divider(label: str = "", color: str = C["dim"]) -> None:
    w = min(_term_width(), 88)
    if label:
        pad = (w - len(label) - 4) // 2
        print(f"\n  {color}{'·' * pad}  {label}  {'·' * pad}{C['reset']}\n")
    else:
        print(f"\n  {color}{'·' * (w - 4)}{C['reset']}\n")


def _print_a2a_header(task: str, agents: list[str], repo: str) -> None:
    w = min(_term_width(), 88)
    agents_str = "  ".join(
        f"{AGENT_COLOR.get(k, C['white'])}{C['bold']}{k}{C['reset']}" for k in agents
    )
    print()
    print(f"{C['bold']}╭{'─' * (w - 2)}╮{C['reset']}")
    print(f"{C['bold']}│{C['reset']}  {C['bold']}DevManager A2A Council{C['reset']}  {C['dim']}—  agents talk to each other{C['reset']}")
    print(f"{C['bold']}│{C['reset']}")
    print(f"{C['bold']}│{C['reset']}  {C['bold']}Task:{C['reset']}    {task[:w - 14]}")
    print(f"{C['bold']}│{C['reset']}  {C['bold']}Agents:{C['reset']}  {agents_str}")
    print(f"{C['bold']}│{C['reset']}  {C['bold']}Repo:{C['reset']}    {C['dim']}{repo}{C['reset']}")
    print(f"{C['bold']}│{C['reset']}")
    print(f"{C['bold']}│{C['reset']}  {C['dim']}Protocol: agents use @agent: message to talk to each other{C['reset']}")
    print(f"{C['bold']}│{C['reset']}  {C['dim']}Mode:     bidirectional · multi-turn · live chat{C['reset']}")
    print(f"{C['bold']}╰{'─' * (w - 2)}╯{C['reset']}")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Single agent call with A2A protocol injected
# ─────────────────────────────────────────────────────────────────────────────

import os, subprocess

def _call_agent(
    agent_key: str,
    prompt: str,
    role: str,
    repo: str,
    timeout: int = 300,
) -> str:
    """Call one CLI agent, stream output, return full text."""
    from .agent_bridge import discover_agents
    agents = discover_agents()
    if agent_key not in agents:
        return f"[{agent_key} not available]"

    info = agents[agent_key]
    cwd = str(Path(repo).expanduser().resolve())
    cmd, stdin_data = _build_command(agent_key, info, prompt, {})
    env = {**os.environ, "FORCE_COLOR": "0", "NO_COLOR": "1"}

    collected: list[str] = []
    first_output = False

    spinner = Spinner(role, agent_key)
    spinner.__enter__()

    try:
        proc = subprocess.Popen(
            cmd, cwd=cwd, env=env,
            stdin=subprocess.PIPE if stdin_data else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
        )
        err_buf: list[bytes] = []
        t_err = threading.Thread(
            target=lambda: [err_buf.append(c) for c in iter(lambda: proc.stderr.read(256), b"")],
            daemon=True,
        )
        t_err.start()

        if stdin_data:
            proc.stdin.write(stdin_data)  # type: ignore[union-attr]
            proc.stdin.close()            # type: ignore[union-attr]

        buf = b""
        for chunk in iter(lambda: proc.stdout.read(64), b""):  # type: ignore[union-attr]
            buf += chunk
            while b"\n" in buf:
                line_b, buf = buf.split(b"\n", 1)
                s = line_b.strip()
                if s and not any(s.startswith(x) for x in _SUPPRESS):
                    if not first_output:
                        spinner.notify_first_output()
                        first_output = True
                    line = line_b.decode("utf-8", errors="replace")
                    print(f"      {line}")
                    collected.append(line)
        if buf.strip() and not any(buf.strip().startswith(x) for x in _SUPPRESS):
            if not first_output:
                spinner.notify_first_output()
            line = buf.decode("utf-8", errors="replace")
            print(f"      {line}")
            collected.append(line)

        proc.wait(timeout=timeout)
        t_err.join()
    except subprocess.TimeoutExpired:
        proc.kill()
        spinner.__exit__(None, None, None)
        print(f"\n      {C['red']}⏱ timed out{C['reset']}")
    finally:
        spinner.__exit__(None, None, None)

    return "\n".join(collected).strip()


def _call_ollama(prompt: str, role: str, cfg: dict) -> str:
    """Call Ollama with spinner, return full text."""
    import json, urllib.request
    base_url = cfg.get("base_url", "http://127.0.0.1:11434")
    model = cfg.get("model", "glm4:latest")
    payload = json.dumps({"model": model, "prompt": prompt, "stream": True}).encode()

    spinner = Spinner(role, "ollama")
    spinner.__enter__()
    first = False
    parts: list[str] = []

    try:
        req = urllib.request.Request(
            f"{base_url}/api/generate", data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            for raw in resp:
                try:
                    obj = json.loads(raw)
                except Exception:
                    continue
                tok = obj.get("response", "")
                if tok:
                    if not first:
                        spinner.notify_first_output()
                        first = True
                        sys.stdout.write("      ")
                        sys.stdout.flush()
                    display = tok.replace("\n", "\n      ")
                    sys.stdout.write(display)
                    sys.stdout.flush()
                    parts.append(tok)
                if obj.get("done"):
                    break
        print()
    except Exception as exc:
        spinner.__exit__(None, None, None)
        print(f"\n      {C['red']}Ollama error: {exc}{C['reset']}")
        return ""
    finally:
        spinner.__exit__(None, None, None)

    return "".join(parts).strip()


# ─────────────────────────────────────────────────────────────────────────────
# A2A Protocol injection — adds communication protocol to any prompt
# ─────────────────────────────────────────────────────────────────────────────

def _inject_protocol(prompt: str, my_name: str, other_agents: list[str], chat_so_far: str) -> str:
    """Add A2A protocol instructions + chat history to a prompt."""
    others = ", ".join(f"@{a}" for a in other_agents if a != my_name)
    protocol = f"""
─────────────────────────────────────────────────────────
COUNCIL PROTOCOL — you are {my_name}, part of a multi-agent council.
Other agents you can talk to: {others}

To address another agent directly, write:
  @agent_name: your question or message

Rules:
- Use @mentions when you need information only another agent can provide
- Be specific in questions — include file paths, function names, line numbers
- Keep messages focused — one question per @mention
- You can ask multiple agents in one response
- After getting replies, incorporate them into your final answer

{('─' * 50) + chr(10) + 'CHAT SO FAR (what other agents have said):' + chr(10) + chat_so_far if chat_so_far else ''}
─────────────────────────────────────────────────────────

{prompt}"""
    return protocol


# ─────────────────────────────────────────────────────────────────────────────
# A2A Council — main orchestrator
# ─────────────────────────────────────────────────────────────────────────────

class A2ACouncil:
    """Multi-agent council where agents genuinely talk to each other.

    Each agent's output is scanned for @mentions. Mentioned agents are called
    immediately to reply. Replies go back into the original agent's follow-up
    context. This repeats until no more @mentions or max_rounds reached.
    """

    MAX_ROUNDS_PER_STEP = 3   # max back-and-forth per pipeline step
    MAX_TOTAL_EXCHANGES = 20  # safety cap on total A2A messages

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
        self.agent_keys = list(self.avail.keys())  # all authenticated agents
        self.session = A2ASession(task=task, repo=repo)
        self._exchange_count = 0

    # ── Public ───────────────────────────────────────────────────────────

    def run(self) -> A2ASession:
        t0 = time.time()
        _print_a2a_header(self.task, self.agent_keys, self.repo)

        # Phase 1: Planner breaks task (Ollama)
        _chat_divider("Phase 1 · Planning", C["dim"])
        plan = self._turn_ollama("planner", self._planner_prompt())
        self._add_msg("ollama", "all", plan, "planner")

        # Phase 2: Multi-agent exploration with A2A
        _chat_divider("Phase 2 · Exploration", C["cyan"])
        explorer_out = self._exploration_phase(plan)

        # Phase 3: Analysis + A2A back-and-forth
        _chat_divider("Phase 3 · Analysis", C["green"])
        analyst_out = self._analysis_phase(plan, explorer_out)

        # Phase 4: Review with A2A challenge
        _chat_divider("Phase 4 · Review", C["yellow"])
        review_out = self._review_phase(analyst_out, explorer_out)

        # Phase 5: Synthesis
        _chat_divider("Phase 5 · Synthesis", C["magenta"])
        final = self._synthesis_phase()

        self.session.final = final
        self.session.total_elapsed = time.time() - t0
        self._print_footer()
        return self.session

    # ── Phases ────────────────────────────────────────────────────────────

    def _exploration_phase(self, plan: str) -> str:
        """Assign each explorer agent, let them ask each other questions."""
        explorers = agents_for_role("explorer") or self.agent_keys
        outputs: list[str] = []

        for key in explorers:
            prompt = self._explorer_prompt(plan)
            out = self._a2a_turn(key, "explorer", prompt)
            outputs.append(f"=== {key} ===\n{out}")

        return "\n\n".join(outputs)

    def _analysis_phase(self, plan: str, explorer_out: str) -> str:
        """Analysts write fixes, can ask explorers for clarification."""
        analysts = agents_for_role("analyst") or self.agent_keys
        outputs: list[str] = []

        for key in analysts:
            prompt = self._analyst_prompt(plan, explorer_out)
            out = self._a2a_turn(key, "analyst", prompt)
            outputs.append(f"=== {key} ===\n{out}")

        return "\n\n".join(outputs)

    def _review_phase(self, analyst_out: str, explorer_out: str) -> str:
        """Reviewers challenge analysts — can ask them to justify their fix."""
        analyst_agents = {m.from_agent for m in self.session.messages if m.role == "analyst"}
        all_agents = agents_for_role("reviewer") or self.agent_keys
        reviewers = [a for a in all_agents if a not in analyst_agents] or all_agents
        outputs: list[str] = []

        for key in reviewers:
            prompt = self._reviewer_prompt(analyst_out, explorer_out)
            out = self._a2a_turn(key, "reviewer", prompt)
            outputs.append(f"=== {key} ===\n{out}")

        return "\n\n".join(outputs)

    def _synthesis_phase(self) -> str:
        """Ollama synthesizes the full chat into a final answer."""
        prompt = self._synthesizer_prompt()
        out = self._turn_ollama("synthesizer", prompt)
        self._add_msg("ollama", "all", out, "synthesizer")
        return out

    # ── A2A Core ─────────────────────────────────────────────────────────

    def _a2a_turn(self, agent_key: str, role: str, base_prompt: str) -> str:
        """Run one agent turn with A2A routing.

        1. Call agent with protocol-injected prompt
        2. Parse @mentions in response
        3. For each mention: call the target agent to reply
        4. Feed replies back to original agent as follow-up
        5. Repeat up to MAX_ROUNDS_PER_STEP times
        """
        known = set(self.agent_keys) | {"ollama", "all"}
        chat_so_far = self._chat_summary()

        # Initial call
        prompt = _inject_protocol(base_prompt, agent_key, self.agent_keys, chat_so_far)
        output = self._call(agent_key, role, prompt)
        self._add_msg(agent_key, "all", output, role)
        _chat_print(self.session.messages[-1])

        # A2A rounds
        rounds = 0
        while rounds < self.MAX_ROUNDS_PER_STEP and self._exchange_count < self.MAX_TOTAL_EXCHANGES:
            mentions = parse_mentions(output, known)
            if not mentions:
                break

            rounds += 1
            replies: list[str] = []

            for target, question in mentions:
                if self._exchange_count >= self.MAX_TOTAL_EXCHANGES:
                    break

                # Show the question as a directed message
                q_msg = Message(
                    from_agent=agent_key, to_agent=target,
                    content=question, role=role, is_question=True,
                )
                self.session.messages.append(q_msg)
                _chat_print(q_msg)
                self._exchange_count += 1

                # Get reply from target agent
                if target == "ollama":
                    reply = self._turn_ollama(
                        role,
                        f"@{agent_key} asked you:\n{question}\n\nContext:\n{self._chat_summary(last_n=4)}\n\nAnswer concisely.",
                    )
                elif target in self.avail:
                    reply_prompt = _inject_protocol(
                        f"@{agent_key} is asking you:\n{question}\n\n"
                        f"Context from earlier:\n{self._chat_summary(last_n=4)}\n\n"
                        f"Answer directly and concisely. You can ask back with @{agent_key}: question",
                        target, self.agent_keys, "",
                    )
                    reply = self._call(target, role, reply_prompt)
                else:
                    reply = f"[{target} not available]"

                r_msg = Message(
                    from_agent=target, to_agent=agent_key,
                    content=reply, role=role, in_reply_to=question,
                )
                self.session.messages.append(r_msg)
                _chat_print(r_msg)
                replies.append(f"@{target} replied: {reply}")

            if not replies:
                break

            # Feed replies back to original agent for a follow-up
            followup_prompt = _inject_protocol(
                f"You asked questions. Here are the replies:\n\n"
                + "\n\n".join(replies)
                + f"\n\nOriginal task context:\n{base_prompt[:1000]}\n\n"
                + "Now incorporate these answers into your analysis. "
                + "You can ask more questions with @agent: or give your final answer.",
                agent_key, self.agent_keys, self._chat_summary(last_n=6),
            )
            output = self._call(agent_key, role, followup_prompt)
            followup_msg = Message(from_agent=agent_key, to_agent="all", content=output, role=role)
            self.session.messages.append(followup_msg)
            _chat_print(followup_msg)

        return output

    # ── Agent callers ─────────────────────────────────────────────────────

    def _call(self, agent_key: str, role: str, prompt: str) -> str:
        acolor = AGENT_COLOR.get(agent_key, C["white"])
        icon = _CHAT_ICON.get(role, "·")
        t0 = time.time()
        print(f"\n  {icon} {acolor}{C['bold']}{agent_key}{C['reset']}  {C['dim']}is thinking…{C['reset']}")
        out = _call_agent(agent_key, prompt, role, self.repo)
        elapsed = time.time() - t0
        print(f"\n  {_elapsed_badge(elapsed, bool(out))}")

        # Permission check for proposed commands
        if self.interactive:
            actions = detect_actions(out)
            risky = [a for a in actions if not a.is_safe and a.kind == "command"]
            for action in risky:
                decision = ask_permission(action, agent_key)
                if decision == "allow":
                    result = run_approved_command(action.value, self.repo)
                    out += f"\n\nCommand result:\n{result}"
                elif decision.startswith("modify:"):
                    result = run_approved_command(decision[len("modify:"):], self.repo)
                    out += f"\n\nCommand result:\n{result}"

        return out

    def _turn_ollama(self, role: str, prompt: str) -> str:
        icon = _CHAT_ICON.get(role, "·")
        print(f"\n  {icon} {C['dim']}{C['bold']}ollama{C['reset']}  {C['dim']}is thinking…{C['reset']}")
        t0 = time.time()
        out = _call_ollama(prompt, role, self.cfg)
        elapsed = time.time() - t0
        print(f"\n  {_elapsed_badge(elapsed, bool(out))}")
        return out or "(no response)"

    # ── Prompt builders ───────────────────────────────────────────────────

    def _planner_prompt(self) -> str:
        return (
            f"You are a dev task planner. Break this task into 2-3 concrete subtasks.\n"
            f"Output ONLY a numbered list. Be specific.\n\n"
            f"Task: {self.task}\n\nRepo context:\n{self.handoff[:600]}"
        )

    def _explorer_prompt(self, plan: str) -> str:
        return (
            f"You are a code explorer. READ ONLY — do not write code.\n\n"
            f"Task: {self.task}\n\nSubtasks:\n{plan}\n\n"
            f"Repo context:\n{self.handoff}\n\n"
            f"Your job:\n"
            f"1. Find exact file paths relevant to each subtask\n"
            f"2. Quote key functions/classes involved\n"
            f"3. Identify root cause or gap\n"
            f"4. If you need another agent to verify something, use @agent: question\n\n"
            f"Other agents in this council: {', '.join(f'@{k}' for k in self.agent_keys)}\n"
            f"Use @mentions to ask them anything you're unsure about."
        )

    def _analyst_prompt(self, plan: str, explorer_out: str) -> str:
        return (
            f"You are an expert software engineer. Write a concrete fix.\n\n"
            f"Task: {self.task}\n\nSubtasks:\n{plan}\n\n"
            f"Explorer findings:\n{explorer_out}\n\n"
            f"Repo context:\n{self.handoff[:2000]}\n\n"
            f"Your job:\n"
            f"1. Root cause (1-2 sentences per subtask)\n"
            f"2. Exact code changes (before/after or diff)\n"
            f"3. Files to touch\n"
            f"4. Risks / side effects\n"
            f"5. Verification commands\n\n"
            f"If you need clarification from another agent, use @agent: question.\n"
            f"Other agents: {', '.join(f'@{k}' for k in self.agent_keys)}"
        )

    def _reviewer_prompt(self, analyst_out: str, explorer_out: str) -> str:
        return (
            f"You are a strict code reviewer. Challenge the proposed fix.\n\n"
            f"Task: {self.task}\n\n"
            f"Explorer findings:\n{explorer_out[:1200]}\n\n"
            f"Proposed fix:\n{analyst_out}\n\n"
            f"Review:\n"
            f"- Is the root cause correct? If not, challenge with @analyst: why?\n"
            f"- Edge cases missed? Ask @analyst: to address them\n"
            f"- Will this break existing tests? Ask @explorer: to verify\n"
            f"- Security implications?\n"
            f"- Is the fix minimal?\n\n"
            f"Use @mentions to challenge specific agents:\n"
            f"{', '.join(f'@{k}' for k in self.agent_keys)}\n\n"
            f"End with: VERDICT: APPROVE / REQUEST_CHANGES"
        )

    def _synthesizer_prompt(self) -> str:
        full_chat = "\n\n".join(
            f"[{m.from_agent} → {m.to_agent}]: {m.content}"
            for m in self.session.messages if m.content
        )
        return (
            f"Synthesize this multi-agent conversation into ONE clear final answer.\n\n"
            f"Task: {self.task}\n\n"
            f"Full agent conversation:\n{full_chat[:6000]}\n\n"
            f"Final answer:\n"
            f"1. Root cause\n"
            f"2. Exact fix (code + files)\n"
            f"3. Key disagreements between agents (if any)\n"
            f"4. Verification steps\n"
            f"5. Open questions / risks\n\n"
            f"Be concise. Highlight where agents agreed vs. disagreed."
        )

    # ── Helpers ───────────────────────────────────────────────────────────

    def _add_msg(self, from_a: str, to_a: str, content: str, role: str) -> None:
        self.session.messages.append(
            Message(from_agent=from_a, to_agent=to_a, content=content, role=role)
        )

    def _chat_summary(self, last_n: int | None = None) -> str:
        msgs = self.session.messages
        if last_n:
            msgs = msgs[-last_n:]
        return "\n\n".join(
            f"[{m.from_agent}→{m.to_agent}]: {m.content[:400]}"
            for m in msgs if m.content
        )

    def _print_footer(self) -> None:
        w = min(_term_width(), 88)
        t = self.session.total_elapsed
        n = len(self.session.messages)
        agents_used = list({m.from_agent for m in self.session.messages})
        exchanges = sum(1 for m in self.session.messages if m.to_agent != "all")

        print()
        _sep("═", C["bold"])
        print(f"{C['bold']}✓  A2A Council complete{C['reset']}  {C['dim']}{t:.1f}s · {n} messages · {exchanges} direct exchanges · agents: {', '.join(agents_used)}{C['reset']}")
        _sep("═", C["bold"])

        # Token usage summary
        try:
            from .token_tracker import print_task_summary
            print_task_summary("A2A session")
        except Exception:
            pass

        if self.interactive:
            try:
                ans = input(f"\n  {C['dim']}Save chat log? [y/N]{C['reset']} > ").strip().lower()
                if ans in ("y", "yes"):
                    p = Path(self.repo) / ".devmanager_a2a.md"
                    p.write_text(self.session.chat_log(), encoding="utf-8")
                    print(f"  {C['green']}✓ Saved:{C['reset']} {p}")
            except (EOFError, KeyboardInterrupt):
                pass
        print()
