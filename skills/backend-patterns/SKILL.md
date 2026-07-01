---
name: backend-patterns
description: NestJS / FastAPI / Express patterns for backend tasks
origin: ECC (adapted)
keywords: [nestjs, fastapi, express, api, service, controller, prisma, redis, queue, bull, dto]
applies_to: [backend]
---

# Backend Patterns

## When to Activate
- Backend API, service layer, or database task
- NestJS module, controller, service, guard, interceptor work
- Redis, Bull queue, Prisma ORM changes

## Key Patterns

### NestJS — Service layer first
- Put business logic in `*.service.ts`, NOT in controllers
- Controllers only validate request → call service → return response
- Use `@Injectable()` for services, inject via constructor

### Error handling
- Throw typed exceptions: `NotFoundException`, `BadRequestException`
- Never swallow errors silently
- Log at service level, not controller

### Prisma
- Use transactions for multi-table writes
- Always handle `P2002` (unique constraint) and `P2025` (not found)

### Redis / Bull
- Idempotent job handlers — safe to retry
- Always set job timeout and retry limits
- Log job ID on enqueue AND completion

## Verification
```bash
npm run build           # TypeScript compile
npm run test            # Jest unit tests
docker compose ps       # Container status
curl http://localhost:4000/health/ready
```
