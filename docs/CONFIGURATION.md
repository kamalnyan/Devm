# Configuration

The manager is designed to be cloned and customized without editing Python.

## Agents

Edit `config/agents.json`.

Each agent has:

- `id`: stable route key, such as `backend` or `reviewer`
- `name`: display name
- `app`: macOS app name under `/Applications`, used for GUI paste
- `description`: short role summary
- `keywords`: words that route a task to this agent
- `prompt_role`: first line of the handoff prompt

List configured agents:

```bash
devm --list-agents
```

## Rules

Rules boost or force routing when multiple keywords appear together.

Example:

```json
{
  "id": "cross_mobile_payment",
  "when_all": ["razorpay|payment|upi|qr", "rider|pilot"],
  "owner": "codex",
  "boost": 6,
  "confidence": "high",
  "reason": "Cross-module payment issue."
}
```

## Prompts

Global `prompt.constraints` and `prompt.deliverables` are injected into every handoff.

Use this for team safety rules, verification expectations, and reporting format.

## Profiles

Edit `config/profiles.json`.

Profiles decide which skills and role presets are expected for a mode:

```bash
devm --list-profiles
devm --profile security "audit payment flow"
```

Built-in profiles:

- `minimal`
- `developer`
- `security`
- `research`

## Role Presets

Role presets live in `config/agents.json` under `role_presets`.

```bash
devm --list-roles
devm --role explorer "inspect this bug"
devm --role reviewer "review this fix"
```

## Skill Library

The `skill_library` section has two buckets:

- `daily`: always useful for selected profiles or common tasks
- `library`: searchable/contextual guidance injected only when keywords match

This follows the ECC DAILY vs LIBRARY pattern without loading every skill into every prompt.

## Validation

```bash
python3 scripts/validate-config.py
devm --doctor
```
