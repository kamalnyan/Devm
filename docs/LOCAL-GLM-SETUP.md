# Local GLM / Low-Memory Model Setup

This manager can use Ollama if it is installed locally.

```bash
ollama serve
ollama pull glm4
DEV_MANAGER_MODEL=glm4:latest python3 manager.py "backend API bug hai"
```

To force GLM usage and stop if the model is not available:

```bash
DEV_MANAGER_REQUIRE_LLM=1 DEV_MANAGER_MODEL=glm4:latest python3 manager.py "frontend responsive issue"
```

No model is mandatory by default. Without Ollama, the manager uses local routing rules. With `DEV_MANAGER_REQUIRE_LLM=1`, it fails clearly instead.

## GLM Choice

- Practical local GLM: `glm4:latest`
- Larger local GLM option: `glm4:9b`
- Cloud GLM option: `glm-5.2`

GLM-5.2 is listed by Ollama as a cloud model, not the small local MB/GB download path. For this laptop manager, start with `glm4:latest`.

## ADK Runner

The ADK runner uses the same local GLM through Ollama:

```bash
.venv-adk/bin/python adk_manager.py --repo /Users/apple/StudioProjects/NewBackend "task"
```

The low-memory path keeps repo inspection deterministic and sends compact evidence to GLM. This avoids hallucinated shell output and avoids exceeding the small Ollama chat context.

The deterministic route remains the source of truth. GLM adds notes and context;
it should not override configured owner/app routing.

## Installed GUI Apps

Claude, Codex, and Antigravity desktop apps can be controlled through macOS GUI automation, not through private APIs:

```bash
.venv-adk/bin/python adk_manager.py --repo /Users/apple/StudioProjects/NewBackend --gui Codex "task"
```

If paste is blocked, enable Accessibility for the terminal/Codex host app in:

`System Settings > Privacy & Security > Accessibility`
