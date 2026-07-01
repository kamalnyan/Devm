---
name: security-review
description: Security checklist for auth, input, secrets, and API safety
origin: ECC (adapted)
keywords: [security, auth, jwt, token, secret, injection, xss, cors, permission, guard]
applies_to: [backend, codex]
---

# Security Review

## When to Activate
- Auth, JWT, session, or permission changes
- Any user input processing
- API endpoint additions
- Secret or credential handling

## Checklist

### Input Validation
- [ ] All user inputs validated at API boundary (DTO / Zod / Pydantic)
- [ ] SQL injection: use ORM parameterized queries — NEVER string concat
- [ ] No `eval()` or `exec()` on user data

### Auth / Sessions
- [ ] JWT verified on every protected route (guard in place)
- [ ] Token expiry set and enforced
- [ ] Refresh token stored httpOnly cookie — NOT localStorage
- [ ] Logout invalidates token server-side

### Secrets
- [ ] Zero secrets in code — use environment variables
- [ ] `.env` in `.gitignore`
- [ ] No secrets logged

### CORS / Headers
- [ ] CORS restricted to known origins — NOT `*` in production
- [ ] Security headers set (Helmet.js / equivalent)

### Rate Limiting
- [ ] Auth endpoints rate-limited
- [ ] Payment/webhook endpoints protected

## Verification
```bash
grep -r "process.env" --include="*.ts" .   # check secret access
grep -rn "console.log" --include="*.ts" .  # check for secret leaks
npm run test -- --testPathPattern=auth
```
