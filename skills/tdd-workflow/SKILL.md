---
name: tdd-workflow
description: Test-driven development — write failing test first, then implement
origin: ECC (adapted)
keywords: [tdd, test, unit, jest, pytest, spec, coverage, mock, stub]
applies_to: [backend, frontend, codex]
---

# TDD Workflow

## When to Activate
- Adding new feature or fixing a bug
- When asked to "write tests" or "add coverage"
- Before any non-trivial implementation

## Red → Green → Refactor

```
1. RED   — write a failing test for the desired behavior
2. GREEN — write the minimal code to make it pass
3. REFACTOR — clean up without breaking tests
```

## Test First Rules
- Write the test BEFORE the implementation
- Test one behavior per test case
- Test names: `describe("UserService") → it("should throw when email taken")`

## What to Test
- Happy path (normal flow)
- Edge cases (empty input, null, zero)
- Error cases (not found, unauthorized, duplicate)
- Contract: what the caller expects, NOT implementation details

## What NOT to Mock
- Business logic — test it directly
- Database: use test DB or in-memory — avoid mocking Prisma

## Coverage Target
- 80%+ line coverage for service layer
- 100% for critical paths (auth, payments)

## Verification
```bash
npm run test -- --coverage    # Jest
pytest -x -q --tb=short       # Python
flutter test --coverage       # Flutter
```
