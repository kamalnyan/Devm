---
name: api-design
description: REST API design patterns — naming, versioning, error responses
origin: ECC (adapted)
keywords: [api, rest, endpoint, route, design, versioning, openapi, swagger, dto, response]
applies_to: [backend, codex]
---

# API Design Patterns

## When to Activate
- Adding or changing API endpoints
- DTO / response shape changes
- Versioning decisions
- OpenAPI / Swagger documentation

## Naming Rules

```
GET    /users            → list users
GET    /users/:id        → get one
POST   /users            → create
PATCH  /users/:id        → partial update
DELETE /users/:id        → delete

# Nested resources
GET    /users/:id/orders → user's orders

# Actions (verbs only when no noun fits)
POST   /payments/:id/capture
POST   /auth/refresh
```

## Response Shape (always consistent)

```json
// Success
{ "data": { ... }, "meta": { "page": 1, "total": 100 } }

// Error
{ "error": { "code": "PAYMENT_FAILED", "message": "...", "details": {} } }
```

## Versioning
- URL prefix: `/api/v1/` for breaking changes
- No version = v1 (implicit)
- Never remove a field — deprecate with `@deprecated` in DTO

## DTOs
- Input DTO: validate all fields at boundary
- Output DTO: never expose internal DB models directly
- Use `class-validator` (NestJS) or Pydantic (FastAPI)
