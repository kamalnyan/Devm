<div align="center">

# DevManager

**An open-source local AI dev routing tool.**  
Route tasks to the right AI agent, run multi-agent councils, and watch agents **talk to each other** in real time.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://python.org)
[![No API Key Required](https://img.shields.io/badge/API%20Key-Not%20Required-green.svg)](#)

```
devm --a2a "fix the razorpay webhook signature bug"
```

```
╭──────────────────────────────────────────────────────────╮
│  DevManager A2A Council  —  agents talk to each other    │
│  Agents:  claude  codex  aider                           │
│  Mode:    bidirectional · multi-turn · live chat         │
╰──────────────────────────────────────────────────────────╯

  ··············  Phase 2 · Exploration  ··············

  🔍 codex  is thinking…
  ⠸  codex  Reading codebase…

      Found webhook handler at src/payments/webhook.ts:47

  ❓  codex  →  @claude
      Did you check if there's a timeout guard? I don't see one on line 52.

  ⚙️  claude  →  @codex
      @codex: Can you confirm what crypto module is imported?

  🔍 codex  →  @claude
      crypto is imported on line 3 but timingSafeEqual is NOT used —
      they're doing plain string comparison. That's the real bug.

  ⚙️  claude
      Root cause confirmed. Fix: crypto.timingSafeEqual() + 30s timeout.
```

</div>

---

## What is DevManager?

DevManager is a **local AI orchestration CLI** that sits between you and your AI tools:

1. **Routes** your dev task to the right AI agent using local rules + optional Ollama
2. **Runs multi-agent councils** where multiple AI agents collaborate sequentially
3. **Enables real A2A communication** — agents `@mention` each other, ask follow-up questions, and reply before giving their final answer
4. **Works entirely offline** (Ollama for planning/synthesis) or with any installed AI CLI — no new API keys needed
5. **Shows everything live** in the terminal — streaming output, spinner animations, permission prompts, agent handoffs

> DevManager doesn't replace your AI tools. It **orchestrates the ones you already have installed**.

---

## Features

| Feature | Command | Description |
|---------|---------|-------------|
| Smart routing | `devm "task"` | Routes to right agent via local rules + Ollama |
| Direct agent | `devm --agent claude "task"` | Send prompt to Claude Code CLI directly |
| Auto agent | `devm --agent auto "task"` | Best available agent auto-selected |
| Council | `devm --council "task"` | Sequential pipeline: Planner→Explorer→Analyst→Reviewer→Synthesizer |
| **A2A Council** | `devm --a2a "task"` | **Agents talk to each other via `@mentions`** |
| Background | `devm --bg --a2a "task"` | Run any mode in background |
| Job management | `devm jobs` / `devm result <id>` | Check background job status and results |
| Agent discovery | `devm agents` | Show all installed AI CLIs with auth status |
| Add custom agent | `devm agent-add mybot --binary /path` | Register any AI CLI |

---

## How A2A Communication Works

Unlike a sequential pipeline, A2A enables **real back-and-forth**:

```
Traditional council:          A2A council:
──────────────────            ──────────────────────────────────
Planner  → text              Planner  → text
   ↓                            ↓
Explorer → text              Explorer → @claude: what's on line 52?
   ↓                            claude  → found the bug here
Analyst  → text              Explorer → (now writes report with that info)
   ↓                            ↓
Reviewer → text              Analyst  → @codex: verify this fix works?
   ↓                            codex   → LGTM but check edge case X
Synthesizer                  Analyst  → ok, adding edge case handler
                             Reviewer → @analyst: justify this choice
                             Analyst  → because of constraint Y
                             Synthesizer → final merged answer
```

Each agent can `@mention` any other agent mid-response. The orchestrator intercepts these, routes them to the target agent, feeds the reply back, and the original agent continues with that new context. Up to 3 rounds of back-and-forth per pipeline step.

---

## Installation

### Prerequisites

- Python 3.11+
- [Ollama](https://ollama.ai) running locally — free, no API key needed
- At least one AI agent CLI installed:
  - [Claude Code](https://claude.ai/code)
  - [Codex CLI](https://chatgpt.com) (via ChatGPT app)
  - [Aider](https://aider.chat) — `pip install aider-chat`
  - [Gemini CLI](https://github.com/google-gemini/gemini-cli) — `npm install -g @google/gemini-cli`
  - Any other AI CLI (register with `devm agent-add`)

### Install

```bash
git clone https://github.com/kamalnyan/devmanager.git
cd devmanager
bash install.sh
```

The installer creates a venv, installs DevManager, and adds `devm` globally.

### Pull Ollama model

```bash
ollama pull glm4
```

Any Ollama model works. GLM4 is recommended.

### Verify

```bash
devm --doctor     # check all components
devm agents       # show discovered AI CLIs
```

---

## Quick Start

```bash
# Route a task (shows which agent + full handoff prompt)
devm "fix the payment webhook bug"

# Send directly to Claude Code CLI
devm --agent claude "fix the payment webhook bug"

# Auto-pick best available agent
devm --agent auto "fix the payment webhook bug"

# Sequential multi-agent council
devm --council "fix the payment webhook bug"

# A2A council — agents talk to each other
devm --a2a "fix the payment webhook bug"

# Run in background
devm --bg --a2a "fix the payment webhook bug"
devm jobs
devm result <job-id>
```

---

## Agent Discovery

DevManager auto-discovers any AI CLI on your machine:

```
devm agents

  AI Agents on this machine:

  ✓  claude          Claude Code
       strengths: backend, implementation, debugging, refactor

  ✓  codex           Codex CLI
       strengths: exploration, review, architecture, cross-stack

  ✓  aider           Aider
       strengths: implementation, refactor, multi-file

  devm --agent auto "task"           → best agent auto-selected
  devm --council "task"              → all agents collaborate
  devm agent-add mybot --binary /path/to/cli  → register new agent
```

### Register any CLI as an agent

```bash
devm agent-add myagent \
  --binary /path/to/myagent-cli \
  --name "My Custom Agent" \
  --stdin \
  --strengths backend review
```

No code changes needed. It immediately participates in future councils.

Built-in auto-discovery: `claude`, `codex`, `aider`, `gemini`, `opencode`, `goose`, `amp`, `gh-copilot`, and PATH scan for `cursor`, `windsurf`, `continue`, `cody`, `tabnine`, and more.

---

## Permission Prompts

When an agent suggests running a command, DevManager asks you first — like Claude Code:

```
  ╭─ 🔐 Permission Request ─────────────────────────────────╮
  │  codex wants to: Run command
  │
  │    npm run test
  │
  ╰──────────────────────────────────────────────────────────╯

  [A]llow  [S]kip  [M]odify  > _
```

- **Safe** (`git status`, `grep`, `cat`) — green badge
- **Normal** (`npm install`, `docker run`) — standard prompt
- **Destructive** (`rm -rf`, `git push --force`, `DROP TABLE`) — red warning

---

## Council Modes

| Mode | Command | How agents communicate |
|------|---------|----------------------|
| `--agent auto` | `devm --agent auto "task"` | Single agent, best match |
| `--council` | `devm --council "task"` | Sequential: A→B→C→D |
| `--a2a` | `devm --a2a "task"` | Bidirectional: agents @mention each other |
| `--adk-council` | `devm --adk-council "task"` | Ollama orchestrates, decides agent flow |

---

## Background Jobs

```bash
devm --bg --a2a "refactor auth to use JWT refresh tokens"
# → ✓ Job ID: 20260701-053444-refactor-auth

devm jobs                        # list all jobs
devm result 20260701-053444      # get result
devm result 2026070105           # short prefix works too
```

---

## Configuration

```bash
devm config                     # view current config

# Set LLM provider
devm config --provider ollama --model glm4:latest
devm config --provider openai --model gpt-4o
devm config --provider anthropic --model claude-opus-4-8
```

Providers: `ollama` (local, free) | `openai` | `anthropic` | `gemini` | `groq` | `together`

---

## Project Structure

```
devmanager/
├── cli.py           # Main entry point — all commands dispatched here
├── a2a.py           # A2A communication — agents @mention each other
├── interactive.py   # Claude Code-style UI — spinner, streaming, permissions
├── council.py       # Sequential multi-agent pipeline
├── agent_bridge.py  # Plugin-style agent discovery + execution
├── handoff.py       # Rich prompt builder — skills, code search, constraints
├── router.py        # Task routing — local rules + optional LLM
├── llm.py           # LLM layer (Ollama + LiteLLM for cloud)
├── solver.py        # Direct LLM call with streaming
├── jobs.py          # Background job management
├── _bg_worker.py    # Detached background process worker
└── doctor.py        # Health check
```

---

## Skills System

DevManager injects skill guidance into agent prompts based on task type:

```
.agents/skills/
├── verification-loop/      # Run build/test/lint after every change
├── payment-integration/    # Razorpay, Stripe, UPI patterns
├── backend-patterns/       # NestJS, Prisma, Redis
├── api-design/             # REST, GraphQL contract design
└── security-review/        # OWASP, auth, injection prevention
```

Add your own:
```bash
mkdir -p .agents/skills/my-skill
# create SKILL.md with YAML frontmatter (triggers, priority, content)
```

---

## Contributing

This project is early-stage and moving fast. Contributions welcome.

```bash
git clone https://github.com/kamalnyan/devmanager.git
cd devmanager
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Areas that need work:
- More agent adapters (Cursor, Windsurf, Continue.dev)
- Web UI for council sessions
- Better A2A routing
- Tests
- Windows support

Open an issue or PR.

---

## License

MIT — see [LICENSE](LICENSE).

---

<div align="center">
Built with Ollama · Claude Code · Codex · Python · Google ADK

**No API keys required to get started.**
</div>
