---
name: verification-loop
description: Run build/test/lint checks before and after every change
origin: ECC (adapted)
keywords: [build, test, lint, verify, check, ci]
applies_to: [backend, frontend, codex]
---

# Verification Loop

## When to Activate
- After ANY code change before marking complete
- When routing a task to any agent
- Before closing a PR or pushing

## Workflow

1. **Read first** — inspect the smallest relevant files before editing
2. **Change** — make the minimal root-cause fix
3. **Verify** — run only the safe commands that apply:

```
npm run build      # compile check
npm run test       # unit tests
npm run lint       # style/type errors
docker compose ps  # container health
curl http://localhost:PORT/health  # runtime ping
```

4. **Report** — state exactly what passed, what failed, what remains unverified

## Rules
- Never skip verification after a change
- Run only allowlisted safe commands (config/safe-commands.json)
- If a command fails, fix root cause — do NOT bypass
- Do not claim "done" without evidence from a verification run
