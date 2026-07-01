---
name: debugging-patterns
description: Systematic debug methodology — read before edit, trace before fix
origin: ECC (adapted)
keywords: [debug, error, crash, issue, investigate, trace, log, exception, stack]
applies_to: [codex, backend, frontend]
---

# Debugging Patterns

## When to Activate
- Any error, crash, or unexpected behavior to investigate
- Cross-module or cross-service issue
- "It works locally but not in prod" type issues

## 5-Step Method

1. **Reproduce** — confirm the exact input/state that triggers the bug
2. **Trace** — follow the data/control flow from entry to failure point
3. **Isolate** — find the smallest code path that fails
4. **Hypothesize** — state the root cause BEFORE changing code
5. **Fix** — make the minimal change, then verify

## Before Touching Code
```
- What is the exact error message / stack trace?
- Where in the call stack does it first go wrong?
- What changed recently? (git log --oneline -10)
- Is it data-specific or always reproducible?
```

## Cross-service Issues
- Trace the request ID across service boundaries
- Check each service's logs in order (producer → consumer)
- Verify serialization: what was sent vs what was received

## Do NOT
- Change multiple things at once ("shotgun debugging")
- Assume the bug is where the error appears — trace upstream
- Skip reproduction — if you can't reproduce, you can't verify the fix

## Verification
After fix, always confirm:
- [ ] Original bug no longer occurs
- [ ] Related paths still work (regression check)
- [ ] Build + tests pass
