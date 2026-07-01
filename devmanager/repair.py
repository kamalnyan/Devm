"""Auto-repair missing config and skill files — inspired by ECC's repair.js."""
from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

_REQUIRED = [
    REPO_ROOT / "config" / "agents.json",
    REPO_ROOT / "config" / "profiles.json",
    REPO_ROOT / "config" / "safe-commands.json",
]

_FALLBACK_AGENTS = {
    "default_owner": "codex",
    "agents": [
        {
            "id": "frontend", "name": "Antigravity", "app": "Antigravity",
            "description": "Frontend/UI/React/Flutter tasks.",
            "keywords": ["react", "next", "ui", "component", "flutter", "css", "screen"],
            "prompt_role": "You are Antigravity. Work on frontend/UI/mobile tasks.",
        },
        {
            "id": "backend", "name": "Claude", "app": "Claude",
            "description": "Backend/API/Docker/Redis tasks.",
            "keywords": ["api", "nestjs", "prisma", "redis", "docker", "service", "payment"],
            "prompt_role": "You are Claude. Work on backend/API/infrastructure tasks.",
        },
        {
            "id": "codex", "name": "Codex", "app": "Codex",
            "description": "Cross-stack debug, architecture, review.",
            "keywords": ["debug", "architecture", "review", "cross-stack", "logs", "ci"],
            "prompt_role": "You are Codex. Debug, review, and reason across the full stack.",
        },
    ],
    "rules": [
        {"when_all": ["payment|razorpay|upi|qr"], "owner": "backend", "boost": 3, "confidence": "high",
         "reason": "Payment task mentions backend/API contract terms."},
        {"when_all": ["docker|redis|prisma|nestjs|migration"], "owner": "backend", "boost": 2, "confidence": "high",
         "reason": "Infrastructure keyword detected."},
        {"when_all": ["component|screen|widget|css|tailwind|next|react"], "owner": "frontend", "boost": 2,
         "confidence": "high", "reason": "UI/component keyword detected."},
        {"when_all": ["debug|investigate|cross-stack|review|architecture"], "owner": "codex", "boost": 2,
         "confidence": "medium", "reason": "Cross-stack investigation keyword."},
    ],
    "role_presets": {
        "planner":  {"label": "Planner",  "prefix": "Plan step-by-step before writing any code."},
        "explorer": {"label": "Explorer", "prefix": "Explore and map the codebase before changing anything."},
        "reviewer": {"label": "Reviewer", "prefix": "Review for correctness, clarity, and regressions."},
        "security": {"label": "Security", "prefix": "Apply OWASP Top 10 and security best practices."},
        "fixer":    {"label": "Fixer",    "prefix": "Make the smallest safe fix; run verification after."},
        "release":  {"label": "Release",  "prefix": "Verify release readiness: tests, docs, changelog."},
    },
    "prompt": {
        "constraints": [
            "Do not use production for testing.",
            "Do not run destructive commands.",
            "Do not deploy, push, migrate, delete data, or change secrets without explicit approval.",
            "Do not assume any AI app CLI/API access unless configured.",
            "Preserve existing user changes.",
            "Read relevant files before editing.",
            "Prefer root-cause fixes over UI fallbacks.",
            "Keep backend contracts and clients aligned.",
        ],
        "deliverables": [
            "What you changed or found.",
            "Verification result (commands run and their output).",
            "Any remaining blocker or unverified risk.",
        ],
    },
    "skill_library": {
        "daily": [
            {"id": "verification-loop", "description": "Run build/test/lint after every change.",
             "keywords": ["build", "test", "verify", "check"]},
        ],
        "library": [
            {"id": "backend-patterns", "description": "NestJS/Prisma/Redis patterns.",
             "keywords": ["nestjs", "prisma", "redis", "service", "api"]},
            {"id": "frontend-patterns", "description": "React/Next.js/Flutter patterns.",
             "keywords": ["react", "next", "flutter", "component", "ui"]},
            {"id": "security-review", "description": "Auth/input/secret security checklist.",
             "keywords": ["security", "auth", "jwt", "secret", "cors"]},
            {"id": "payment-integration", "description": "Razorpay/UPI/QR payment patterns.",
             "keywords": ["razorpay", "upi", "qr", "payment", "webhook"]},
            {"id": "docker-patterns", "description": "Docker/compose health and networking.",
             "keywords": ["docker", "compose", "redis", "health", "container"]},
            {"id": "debugging-patterns", "description": "Systematic debug: reproduce→trace→fix.",
             "keywords": ["debug", "error", "trace", "investigate", "crash"]},
            {"id": "api-design", "description": "REST API naming, versioning, DTOs.",
             "keywords": ["api", "rest", "endpoint", "dto", "design"]},
            {"id": "tdd-workflow", "description": "Red→Green→Refactor TDD cycle.",
             "keywords": ["tdd", "test", "jest", "coverage", "spec"]},
        ],
    },
}

_FALLBACK_PROFILES = {
    "default": "developer",
    "profiles": {
        "minimal": {
            "id": "minimal",
            "description": "Minimal context — no hooks, rules-only routing. Fastest.",
            "daily_skills": [],
            "roles": ["fixer"],
            "constraints": ["Use local rules only. No LLM required."],
        },
        "core": {
            "id": "core",
            "description": "Core profile with verification loop. Good for most tasks.",
            "daily_skills": ["verification-loop"],
            "roles": ["fixer", "explorer"],
            "constraints": ["Read before edit.", "Run verification after change."],
        },
        "developer": {
            "id": "developer",
            "description": "Default engineering profile for app/backend work.",
            "daily_skills": ["verification-loop"],
            "roles": ["explorer", "fixer", "reviewer"],
            "constraints": [
                "Use evidence from the current repo before routing.",
                "Prefer root-cause fixes and local verification.",
            ],
        },
        "security": {
            "id": "security",
            "description": "Security-focused profile. Activates OWASP checklist on every task.",
            "daily_skills": ["verification-loop", "security-review"],
            "roles": ["security", "reviewer"],
            "constraints": [
                "Apply OWASP Top 10 checklist to every change.",
                "Flag security issues CRITICAL/HIGH/MEDIUM/LOW.",
                "Never approve code with hardcoded secrets.",
            ],
        },
        "research": {
            "id": "research",
            "description": "Research and exploration profile. Minimises changes, maximises reads.",
            "daily_skills": ["continuous-learning"],
            "roles": ["explorer", "planner"],
            "constraints": [
                "Explore and map before suggesting any change.",
                "Produce a written analysis before writing code.",
            ],
        },
        "full": {
            "id": "full",
            "description": "Full profile — all skills active, strictest constraints.",
            "daily_skills": [
                "verification-loop", "security-review", "tdd-workflow", "continuous-learning",
            ],
            "roles": ["planner", "explorer", "fixer", "reviewer", "security", "release"],
            "constraints": [
                "Apply all skill guidance on every task.",
                "TDD required for any new function.",
                "Security checklist on any auth/payment/input change.",
                "Verify build + tests before marking complete.",
            ],
        },
    },
}

_FALLBACK_SAFE_COMMANDS = {
    "_comment": "Edit 'allowed' to match your project. Commands NOT listed here are blocked.",
    "allowed": [
        "npm run build", "npm run test", "npm run lint", "npm run typecheck",
        "cd backend && npm run build", "cd backend && npm run test",
        "cd frontend && npm run lint", "cd frontend && npm run build",
        "docker compose up -d --build", "docker compose down",
        "docker compose ps", "docker compose logs --tail=50",
        "curl -fsS http://localhost:3000/health",
        "curl -fsS http://localhost:4000/health/ready",
        "flutter analyze", "flutter test",
        "cargo test", "go test ./...", "pytest -x -q",
    ],
    "blocked_by_default": [
        "deploy", "push", "prisma migrate dev", "prisma migrate deploy",
        "docker compose down -v", "rm -rf", "production", "secret", "password",
    ],
}


def repair(dry_run: bool = False) -> list[dict]:
    results = []
    for path in _REQUIRED:
        if path.exists():
            results.append({"file": str(path), "status": "ok", "action": "none"})
            continue
        if dry_run:
            results.append({"file": str(path), "status": "missing", "action": "would-create"})
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        fallback = _fallback_for(path)
        path.write_text(json.dumps(fallback, indent=2, ensure_ascii=False), encoding="utf-8")
        results.append({"file": str(path), "status": "missing", "action": "created"})
    return results


def print_repair(dry_run: bool = False) -> None:
    results = repair(dry_run=dry_run)
    print(f"{'Dry run — ' if dry_run else ''}DevManager Repair\n")
    all_ok = True
    for item in results:
        if item["status"] == "ok":
            print(f"  ✓ OK      {item['file']}")
        elif item["action"] in ("created", "would-create"):
            all_ok = False
            verb = "Would create" if dry_run else "Created"
            print(f"  ✚ {verb} {item['file']}")
    if all_ok:
        print("\nAll config files present. Nothing to repair.")
    elif not dry_run:
        print("\nRepair complete. Run 'devm --doctor' to verify.")


def _fallback_for(path: Path) -> dict:
    name = path.name
    if name == "agents.json":
        return _FALLBACK_AGENTS
    if name == "profiles.json":
        return _FALLBACK_PROFILES
    if name == "safe-commands.json":
        return _FALLBACK_SAFE_COMMANDS
    return {}
