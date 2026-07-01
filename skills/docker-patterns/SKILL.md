---
name: docker-patterns
description: Docker / docker compose health, networking, volume patterns
origin: ECC (adapted)
keywords: [docker, compose, container, redis, postgres, health, volume, network, restart]
applies_to: [backend, codex]
---

# Docker Patterns

## When to Activate
- Docker compose up/down issues
- Container health check failures
- Redis/Postgres connection refused inside container
- Volume mount or networking problems

## Health Checks

```yaml
# docker-compose.yml — always add healthcheck
services:
  api:
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:3000/health"]
      interval: 10s
      timeout: 5s
      retries: 3
      start_period: 15s
  redis:
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
```

## Common Issues

### "Connection refused" inside container
- Use service name as hostname: `redis://redis:6379` NOT `localhost`
- Check `depends_on: {redis: {condition: service_healthy}}`
- Wait for health, not just container start

### Volume permissions
```bash
# Fix ownership inside container
docker compose exec api chown -R node:node /app/uploads
```

### Redis persistence
- `appendonly yes` in redis.conf for durability
- Map volume: `./redis-data:/data`

## Verification
```bash
docker compose ps                          # container status
docker compose logs --tail=50 api          # app logs
docker compose exec redis redis-cli ping   # redis health
curl -fsS http://localhost:4000/health/ready
```
