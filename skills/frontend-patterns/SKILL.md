---
name: frontend-patterns
description: React / Next.js / Flutter patterns for frontend/UI tasks
origin: ECC (adapted)
keywords: [react, next, nextjs, flutter, dart, component, hook, state, ui, screen, widget]
applies_to: [frontend]
---

# Frontend Patterns

## When to Activate
- UI component, screen, or page task
- React/Next.js hook or state management
- Flutter widget or navigation

## React / Next.js

### Component rules
- One concern per component — split if >150 lines
- Use named exports, not default (easier refactoring)
- Custom hooks for reusable logic: `use<Feature>.ts`

### State
- Local state: `useState` / `useReducer`
- Server state: React Query / SWR — NOT in Redux
- Global app state only: Zustand or Context

### Next.js
- Prefer Server Components — use `"use client"` only when needed
- API routes for BFF logic, not for business logic
- Images: always use `next/image` with explicit dimensions

## Flutter

### Widget rules
- `StatelessWidget` by default, `StatefulWidget` only when local state needed
- Extract to methods only if >3 params — else extract to Widget class
- Use `const` constructors wherever possible

### State (Flutter)
- Riverpod for app state, `setState` for pure UI-local state
- AsyncValue for loading/error/data states

## Verification
```bash
npm run lint && npm run build   # Next.js
flutter analyze && flutter test  # Flutter
```
