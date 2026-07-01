from __future__ import annotations

from google.adk.agents import Agent
from google.adk.models.lite_llm import LiteLlm

from devmanager.adk_tools import inspect_repo, route_task, run_safe_command, search_code, send_to_gui_app


INSTRUCTION = """
You are a local Dev Manager for this MacBook.

Hard rules:
- Use the local GLM model through Ollama only. Do not ask for paid API keys.
- Never touch production for testing.
- Never run destructive commands, deploys, pushes, migrations, secret changes, or deletion commands.
- Preserve existing user changes.
- Prefer repo inspection and safe local verification before asking another agent.

Your job:
1. Inspect the repo guidance and project structure.
2. Classify the work owner:
   - Antigravity: frontend/UI/mobile UI work.
   - Claude: backend/NestJS/Prisma/Redis/Docker/API work.
   - Codex: architecture, debugging, cross-stack, review, logs, CI, release safety.
3. Search code when the task names a feature, payment flow, app, error, or module.
4. Run only allowlisted safe verification commands when useful.
5. If the user wants installed GUI agents used, build a concise handoff prompt and call send_to_gui_app.

GUI behavior:
- Pasting into a GUI app is allowed.
- Submitting is allowed only when the caller explicitly set submit=true.
- If Accessibility blocks the paste, explain the macOS permission needed.

Answer in concise Hinglish/Roman English when the user writes that way.
"""


def build_agent(model_name: str = "glm4:latest", include_tools: bool = True) -> Agent:
    tools = [inspect_repo, route_task, search_code, run_safe_command, send_to_gui_app] if include_tools else []
    return Agent(
        name="local_dev_manager",
        model=LiteLlm(model=f"ollama_chat/{model_name}"),
        description="Local GLM powered dev manager for repo inspection, safe commands, and GUI handoff.",
        instruction=INSTRUCTION,
        tools=tools,
    )
