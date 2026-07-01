# ADK Bridge Notes

This folder is named `dev-manager-adk` because it is the local manager/orchestrator layer.
It now has two runners:

- `manager.py`: lightweight deterministic router with optional Ollama/GLM prompt improvement.
- `adk_manager.py`: Google ADK runner using local GLM through Ollama/LiteLLM, plus GUI handoff.

Why the lightweight runner still exists:

- It is faster.
- It is reliable when GLM context is too small.
- It works even if ADK dependencies break.

Practical mapping:

- ADK agent instruction -> `adk_dev_manager/agent.py`
- repo inspection/search/safe commands -> `adk_dev_manager/tools.py`
- GUI app paste bridge -> `dev_manager/gui_bridge.py`
- local GLM model -> `LiteLlm(model="ollama_chat/glm4:latest")`

GUI handoff:

```bash
.venv-adk/bin/python adk_manager.py --repo /Users/apple/StudioProjects/NewBackend --gui Codex "task text"
```

This activates `/Applications/Codex.app`, copies the handoff prompt to the clipboard, and pastes it. It presses Return only when `--submit` is passed.

Configured app names come from `config/agents.json`, not from hardcoded Python.
Run:

```bash
devm --list-agents
```

to see what the current clone knows how to route.

Additional local control commands:

```bash
devm --doctor
devm --list-profiles
devm --list-roles
```

If a proper ADK + Ollama adapter is added later, keep the same safety rules:

- no production testing
- no destructive commands
- no deploy/push/migrate without explicit approval
- use GUI paste only for the allowlisted apps: Codex, Claude, Antigravity
