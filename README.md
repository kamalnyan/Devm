<div align="center">

<img src="https://raw.githubusercontent.com/kamalnyan/Devm/main/docs/assets/banner.png" alt="DevManager" width="100%" />

# ⚡ Devm

### Your AI agents, finally talking to each other.

> Route dev tasks to the right AI · Run multi-agent councils · Watch agents `@mention` each other in real time

<br/>

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Ollama](https://img.shields.io/badge/Ollama-Local%20AI-black?style=for-the-badge)](https://ollama.ai)
[![No API Key](https://img.shields.io/badge/API%20Key-Not%20Required-22c55e?style=for-the-badge)](#)

<br/>

```bash
pip install devm   # coming soon · for now: bash install.sh
```

<br/>

</div>

---

## 🤔 What is this?

You have Claude Code installed. Maybe Codex too. Maybe Aider. They're all sitting on your machine doing nothing until you manually open them one by one.

**Devm fixes that.**

It's a single `devm` command that:

- 🎯 **Routes** your task to the right agent automatically
- 🤝 **Runs councils** where multiple agents work together
- 💬 **Lets agents talk to each other** — Claude asks Codex a question, Codex replies, Claude uses that to write a better fix
- 📺 **Shows everything live** in your terminal with a Claude Code-style UI
- 🔐 **Asks permission** before running any command an agent suggests
- 🔋 **Works offline** — Ollama handles planning and synthesis, no API key needed

<br/>

---

## 🎬 See it in action

```
$ devm --a2a "fix the razorpay webhook signature bug"
```

```
╭──────────────────────────────────────────────────────────╮
│  ⚡ Devm A2A Council  —  agents talking to each other    │
│                                                          │
│  Task:    fix the razorpay webhook signature bug         │
│  Agents:  claude  codex  aider                           │
│  Mode:    bidirectional · multi-turn · live chat         │
╰──────────────────────────────────────────────────────────╯

  ·················  🧠 Planning  ··················

  🧠 ollama  is thinking…
  ⠸  ollama  Breaking down the task…

      1. Find the webhook handler and signature logic
      2. Identify the bug — string compare vs timing-safe
      3. Write the fix with proper crypto validation

  ✓  8.3s

  ················  🔍 Exploration  ················

  🔍 codex  Scanning repo…
  ⠼  codex  Reading codebase…

      Found: src/payments/webhook.ts:47
      Signature check on line 52 — using plain string comparison

  ❓  codex  →  @claude
      There's no timeout guard either. Did you want me to check
      if crypto is even imported correctly?

  ⚙️  claude  →  @codex
      Yes please. @codex: what's the import on line 1-5?

  🔍 codex  →  @claude
      crypto is imported but timingSafeEqual is NOT used.
      Line 52: if (sig === expected) — that's the bug.
      Also no timeout, so replay attacks are possible.

  ⚙️  claude
      Got it. Root cause: string comparison + no timeout.

      Fix:
      - Replace with crypto.timingSafeEqual(Buffer.from(sig), Buffer.from(expected))
      - Add 5 minute timestamp window check

  🔎 reviewer  →  @claude
      What about the case where X-Razorpay-Signature header is missing?
      Should return 400, not crash.

  ⚙️  claude  →  @reviewer
      Good catch. Adding null guard before the comparison.

  ✨ Synthesizing final answer…

  ════════════════════════════════════════════════════════
  ✓  Council complete  · 6 messages · 4 direct exchanges
  ════════════════════════════════════════════════════════
```

<br/>

---

## ✨ All Commands

| Command | What it does |
|---------|-------------|
| `devm "task"` | Smart route — figures out which agent should handle it |
| `devm --agent claude "task"` | Send directly to Claude Code CLI |
| `devm --agent codex "task"` | Send directly to Codex CLI |
| `devm --agent auto "task"` | Auto-pick the best available agent |
| `devm --council "task"` | Sequential pipeline: each agent builds on the last |
| `devm --a2a "task"` | **Agents talk to each other via @mentions** |
| `devm --solve "task"` | Ollama solves it locally, streams to terminal |
| `devm --bg --a2a "task"` | Run in background, check result later |
| `devm jobs` | See all background jobs |
| `devm result <id>` | Get result of a background job |
| `devm agents` | See all discovered AI CLIs + auth status |
| `devm agent-add` | Register any AI CLI as an agent |
| `devm config` | View / update settings |
| `devm --doctor` | Health check — Ollama, agents, config |
| `devm history` | Recent tasks and results |
| `devm consult` | Skill recommendations for your task |

<br/>

---

## 🚀 Getting Started

### 1. Install Ollama (free local AI)

```bash
# macOS
brew install ollama
ollama serve &
ollama pull glm4
```

Ollama handles planning and synthesis — no API key, runs on your machine.

### 2. Have at least one AI CLI installed

| Agent | Install |
|-------|---------|
| [Claude Code](https://claude.ai/code) | Download the app, `claude` CLI included |
| [Codex CLI](https://chatgpt.com) | Download ChatGPT app, `codex` CLI included |
| [Aider](https://aider.chat) | `pip install aider-chat` |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | `npm install -g @google/gemini-cli` |

### 3. Install Devm

```bash
git clone https://github.com/kamalnyan/Devm.git
cd Devm
bash install.sh
```

That's it. `devm` is now globally available.

### 4. Verify everything works

```bash
devm --doctor
devm agents
```

<br/>

---

## 💬 How A2A Actually Works

This is the interesting part. Most "multi-agent" tools just pass text from one agent to the next in a chain. That's not communication, that's a conveyor belt.

**A2A is different.** Agents can interrupt, ask questions, and get answers before they finish their work:

```
❌ Old way (conveyor belt):
   Planner → Explorer → Analyst → Reviewer
   (each one just reads the previous output, no back-and-forth)

✅ A2A way (real conversation):
   Explorer:  "I found the bug at line 52"
   Analyst:   @codex: "Can you confirm the import on line 3?"
   Codex:     "timingSafeEqual is imported but not used"
   Analyst:   (now writes a better fix using that info)
   Reviewer:  @analyst: "What about missing header edge case?"
   Analyst:   "Good catch, adding null guard"
   Synthesizer: merges everything into final answer
```

Each agent can send up to 3 rounds of questions per step. The orchestrator intercepts `@mentions`, routes them, gets replies, and feeds them back. All shown live in your terminal.

<br/>

---

## 🔌 Auto-Discovery — It Finds Your Agents

Devm automatically scans your machine for installed AI CLIs:

```
$ devm agents

  AI Agents on this machine:

  ✓  claude     Claude Code
                strengths: backend, implementation, debugging

  ✓  codex      Codex CLI
                strengths: exploration, review, architecture

  ✓  aider      Aider
                strengths: implementation, refactor, multi-file

  +2 more discovered but not authenticated. Run 'devm agents --all' to see them.

  devm --agent auto "task"    → best agent auto-selected
  devm --a2a "task"           → all agents collaborate
  devm agent-add mybot ...    → register a new agent
```

**Add any AI CLI in 10 seconds:**

```bash
devm agent-add mytool \
  --binary /usr/local/bin/mytool \
  --name "My AI Tool" \
  --strengths backend review
```

No code changes. It joins every future council automatically.

**Built-in discovery for:** `claude` · `codex` · `aider` · `gemini` · `opencode` · `goose` · `amp` · `gh-copilot` · `cursor` · `windsurf` · `continue` · `cody` · `tabnine` and more via PATH scan.

<br/>

---

## 🔐 Permission Prompts

When an agent suggests running a command, Devm asks you first:

```
  ╭─ 🔐 Permission Request ──────────────────────────────╮
  │  codex wants to run:                                  │
  │                                                       │
  │    npm run test                                       │
  │                                                       │
  ╰───────────────────────────────────────────────────────╯

  [A]llow   [S]kip   [M]odify  >  _
```

**Three levels of risk:**

- 🟢 `git status`, `grep`, `cat` — read-only, auto-allowed
- 🟡 `npm install`, `docker run` — standard prompt
- 🔴 `rm -rf`, `git push --force`, `DROP TABLE` — red warning, extra confirmation

You can also modify a command before running it.

<br/>

---

## ⚙️ Configuration

```bash
devm config                   # see current settings

# Use local Ollama (default, free)
devm config --provider ollama --model glm4:latest

# Use OpenAI
devm config --provider openai --model gpt-4o

# Use Anthropic
devm config --provider anthropic --model claude-opus-4-8

# Use Groq (fast + cheap)
devm config --provider groq --model llama-3.1-70b-versatile
```

Config is saved to `~/.devmanager/config.json`.

<br/>

---

## 🗂 Background Jobs

Don't want to wait? Run it in the background:

```bash
# Start
devm --bg --a2a "refactor the auth module to use JWT refresh tokens"
# → ✓ Job ID: 20260701-053444-refactor-auth

# Check status
devm jobs

# Get result when done
devm result 20260701-053444
# Short prefix also works
devm result 202607
```

<br/>

---

## 🧠 Skills System

Devm injects domain-specific guidance into agent prompts automatically. Drop a `SKILL.md` in `.agents/skills/` and it gets picked up:

```
.agents/skills/
├── verification-loop/      # Always run tests after changes
├── payment-integration/    # Razorpay, Stripe, UPI patterns
├── backend-patterns/       # NestJS, Prisma, Redis
├── api-design/             # REST, GraphQL contracts
├── security-review/        # OWASP, auth, injection
└── your-custom-skill/      # Add your own
```

Skills are matched by keyword. The right ones get injected automatically.

<br/>

---

## 🏗 How It's Built

```
devmanager/
├── a2a.py           # A2A engine — @mention routing, message bus
├── interactive.py   # Terminal UI — spinner, streaming, permissions
├── council.py       # Sequential pipeline mode
├── agent_bridge.py  # Agent plugin registry — discover + run any CLI
├── cli.py           # All commands
├── handoff.py       # Prompt builder — skills + code search + constraints
├── router.py        # Task routing — local rules + Ollama
├── solver.py        # Direct LLM call with streaming
├── jobs.py          # Background job management
└── doctor.py        # Health check
```

<br/>

---

## 🤝 Contributing

This is early and moving fast. All contributions welcome.

```bash
git clone https://github.com/kamalnyan/Devm.git
cd Devm
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

**Good first issues:**
- Add agent adapter for Cursor / Windsurf / Continue.dev
- Write tests for the A2A mention parser
- Add Windows support
- Build a web UI for council sessions

Open an issue, open a PR, or just star the repo if you find it useful.

<br/>

---

## 📄 License

MIT — see [LICENSE](LICENSE). Use it however you want.

<br/>

---

<div align="center">

Built with [Ollama](https://ollama.ai) · [Claude Code](https://claude.ai/code) · [Codex](https://openai.com) · Python

**No API keys required to get started.**

⭐ Star this repo if it saved you time

</div>
