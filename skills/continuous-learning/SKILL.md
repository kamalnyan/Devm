---
name: continuous-learning
description: Extract reusable patterns from completed tasks and save to skills
origin: ECC (adapted)
keywords: [learn, pattern, extract, improve, memory, skill]
applies_to: [codex]
---

# Continuous Learning

## When to Activate
- After completing a non-trivial task
- After resolving a recurring bug type
- After finding a pattern worth reusing

## Pattern Extraction

After completing a task, ask:
1. What was the root cause pattern? (not just this bug, the class of bug)
2. What diagnostic steps worked best?
3. What verification command proved the fix?
4. Would this pattern help in other repos?

## Saving a Pattern

Document in `skills/<pattern-name>/SKILL.md`:
```markdown
---
name: <slug>
description: <one line>
keywords: [relevant, terms]
---

## When to Activate
[Trigger scenario]

## Pattern
[What to do]

## Verification
[How to confirm it worked]
```

## Anti-patterns to Note
Also capture what NOT to do:
- What misleading error message you chased
- What "obvious fix" that didn't work
- What the real root cause turned out to be
