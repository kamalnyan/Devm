---
name: payment-integration
description: Razorpay / UPI / QR payment flow patterns and debug checklist
origin: DevManager custom
keywords: [razorpay, upi, qr, payment, webhook, capture, vpa, collect, pg, order]
applies_to: [backend, codex]
---

# Payment Integration Patterns

## When to Activate
- Razorpay order, payment capture, webhook task
- UPI collect / QR flow issues
- Payment status mismatch or webhook not firing

## Razorpay Flow

```
Create Order → SDK/QR shown to user → Payment attempt
→ Webhook fires (payment.captured / payment.failed)
→ Backend verifies signature → Update DB → Release goods
```

## Common Issues & Checks

### Webhook not firing
- [ ] Webhook URL publicly accessible (not localhost)
- [ ] Razorpay dashboard → Webhooks → check event log
- [ ] `razorpay-signature` header verified with HMAC-SHA256
- [ ] Endpoint returns HTTP 200 within 5s

### UPI collect timeout
- [ ] VPA validated before sending collect request
- [ ] Timeout handled — poll `GET /payments/:id` for status
- [ ] Idempotency: never create duplicate order for same attempt

### QR code flow
- [ ] QR generated with correct `amount` (in paise, NOT rupees)
- [ ] QR type: `upi_qr` for UPI, `bharat_qr` for card+UPI
- [ ] Poll payment status — QR payments are async

## Verification
```bash
# Check webhook secret
grep -r "RAZORPAY" .env.example
# Check signature verification
grep -rn "validateWebhookSignature\|razorpay_signature" --include="*.ts" .
# Test order creation
curl -X POST http://localhost:4000/api/payments/create-order -H "Content-Type: application/json" -d '{"amount":100}'
```
