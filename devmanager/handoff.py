from __future__ import annotations

import re
import subprocess
import shutil
from pathlib import Path

from devmanager.agent_config import agent_for_owner, load_agent_config, matched_skills, profile_by_name, role_preset

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"
MAX_SKILL_CHARS = 1200
MAX_GUIDANCE_CHARS = 1500
MAX_SEARCH_HITS = 8
MAX_HIT_LINE = 120
MAX_SNIPPET_LINES = 3  # lines of context to show per file hit


def build_handoff(task: str, route: dict, context: dict, used_llm: bool, profile: str | None = None, role: str | None = None) -> dict:
    config = load_agent_config()
    owner = route["owner"]
    agent = agent_for_owner(owner, config)
    target_app = route.get("target_app") or agent.get("app") or agent["name"]
    safe_commands = _safe_commands(owner, context)
    selected_profile = profile_by_name(profile)
    selected_role = role_preset(role, config)
    skills = matched_skills(task, selected_profile["id"], config)
    prompt = _prompt_for_owner(task, owner, context, safe_commands, agent, config, selected_profile, selected_role, skills)

    return {
        "owner": owner,
        "target_app": target_app,
        "target_name": agent["name"],
        "profile": selected_profile["id"],
        "role": selected_role["id"] if selected_role else None,
        "skills": skills,
        "confidence": route.get("confidence", "medium"),
        "mode": route.get("source", "local-rules") if used_llm else "local-rules",
        "reason": route.get("reason", ""),
        "missing_guidance": context.get("missing_guidance", []),
        "warnings": context.get("warnings", []),
        "safe_commands": safe_commands,
        "prompt": prompt,
    }


def _prompt_for_owner(task, owner, context, safe_commands, agent, config, profile, role, skills) -> str:
    repo_root = context["repo_root"]
    projects = context.get("projects", {})

    owner_focus = agent["prompt_role"]
    project_hint = _project_hint(owner, projects)
    commands = "\n".join(f"- `{cmd}`" for cmd in safe_commands) or "- No safe command suggestion available."
    warnings = "\n".join(f"- {w}" for w in context.get("warnings", [])) or "- None."
    constraints = _bullets(config.get("prompt", {}).get("constraints", []))
    profile_constraints = _bullets(profile.get("constraints", []))
    deliverables = _bullets(config.get("prompt", {}).get("deliverables", []))
    project_summary = _project_summary(projects)
    role_block = _role_block(role)

    # FIX 2: Embed actual skill content (not just names)
    skill_block = _skill_block_with_content(skills)

    # FIX 3: Embed guidance file content (not just paths)
    guidance_block = _guidance_block_with_content(context)

    # FIX 5: Code search — include relevant file hits in prompt
    search_block = _search_block(repo_root, task)

    return f"""{owner_focus}
{role_block}

Repo root: `{repo_root}`

User task:
{task}

Assigned owner:
- Owner: `{owner}` → `{agent.get("name")}` / `{agent.get("app")}`
- Profile: `{profile.get("id")}` — {profile.get("description")}

Operating constraints:
{constraints}

Profile constraints:
{profile_constraints}

Skill guidance:
{skill_block}

Repo guidance (AGENTS.md / HARNESS.md / ACCESS-MODEL.md):
{guidance_block}

Relevant code found in repo:
{search_block}

Context warnings:
{warnings}

Known project areas:
{project_summary}

Suggested area for this task:
{project_hint}

Safe verification commands:
{commands}

Required workflow:
1. Read guidance above and inspect the smallest relevant code paths.
2. State the suspected root cause / data flow before making any change.
3. If cross-module: keep producer and consumer contracts aligned.
4. Make the smallest root-cause fix.
5. Run only the applicable safe verification commands.
6. Report exactly what was changed and what remains unverified.

Deliver:
{deliverables}
"""


# ---------------------------------------------------------------------------
# FIX 2 — Skill content embedding
# ---------------------------------------------------------------------------

def _skill_block_with_content(skills: dict) -> str:
    lines = []
    for label, items in (("DAILY skills", skills.get("daily", [])), ("Matched skills", skills.get("library", []))):
        if not items:
            continue
        lines.append(f"### {label}")
        for item in items:
            lines.append(f"\n**{item['id']}** — {item.get('description', '')}")
            content = _read_skill_content(item["id"])
            if content:
                lines.append(content)
    return "\n".join(lines) if lines else "- No specific skill guidance matched."


def _read_skill_content(skill_id: str) -> str:
    skill_file = SKILLS_DIR / skill_id / "SKILL.md"
    if not skill_file.exists():
        return ""
    raw = skill_file.read_text(encoding="utf-8", errors="replace")
    # Strip frontmatter
    if raw.startswith("---"):
        end = raw.find("---", 3)
        if end != -1:
            raw = raw[end + 3:].lstrip()
    # Strip h1 title (redundant — we already show skill_id)
    raw = re.sub(r"^#\s+.+\n", "", raw, count=1)
    return raw[:MAX_SKILL_CHARS].rstrip() + ("\n...(truncated)" if len(raw) > MAX_SKILL_CHARS else "")


# ---------------------------------------------------------------------------
# FIX 3 — Guidance file content embedding
# ---------------------------------------------------------------------------

def _guidance_block_with_content(context: dict) -> str:
    guidance = context.get("guidance", {})
    missing = context.get("missing_guidance", [])
    lines = []
    for name, entries in guidance.items():
        for entry in entries[:1]:  # first match per name
            path = entry.get("path", "")
            text = (entry.get("text") or "").strip()
            if text:
                lines.append(f"**{name}** (`{path}`):")
                lines.append(text[:MAX_GUIDANCE_CHARS])
                if len(text) > MAX_GUIDANCE_CHARS:
                    lines.append("...(truncated)")
                lines.append("")
    if missing and not lines:
        return f"- Guidance files not found in repo: {', '.join(missing)}\n  Add AGENTS.md to this repo to give DevManager richer context."
    if missing:
        lines.append(f"- Missing: {', '.join(missing)}")
    return "\n".join(lines) if lines else "- No guidance files found."


# ---------------------------------------------------------------------------
# FIX 5 — Code search
# ---------------------------------------------------------------------------

def _search_block(repo_root: str, task: str) -> str:
    terms = _extract_search_terms(task)
    if not terms:
        return "- No specific code terms found to search."

    rg = _find_rg()
    # file path → list of (line_no, snippet) tuples
    hits: dict[str, list[tuple[int, str]]] = {}

    for term in terms[:5]:
        if rg:
            # Search with context: show matching lines (not just file paths)
            cmd = [
                rg, "--line-number", "--no-heading", "--hidden", "--max-depth", "6",
                "-g", "!node_modules", "-g", "!dist", "-g", "!.git",
                "-g", "!.next", "-g", "!build", "-g", "!coverage",
                "-g", "!__pycache__", "--max-count", "3",
                term, repo_root,
            ]
        else:
            cmd = [
                "grep", "-rn", "--include=*.ts", "--include=*.js", "--include=*.py",
                "--include=*.go", "--include=*.dart",
                "--exclude-dir=.git", "--exclude-dir=node_modules",
                "--exclude-dir=dist", "-m", "3",
                term, repo_root,
            ]
        try:
            result = subprocess.run(cmd, text=True, capture_output=True, timeout=10, check=False)
            for line in result.stdout.splitlines():
                # rg format: /path/file.ts:42:    const razorpay = ...
                parts = line.split(":", 2)
                if len(parts) >= 3:
                    fpath, lineno_str, snippet = parts[0], parts[1], parts[2]
                    try:
                        lineno = int(lineno_str)
                    except ValueError:
                        continue
                    if fpath not in hits:
                        hits[fpath] = []
                    if len(hits[fpath]) < MAX_SNIPPET_LINES:
                        hits[fpath].append((lineno, snippet.strip()[:100]))
        except (subprocess.TimeoutExpired, OSError):
            pass

    if not hits:
        return f"- No files found matching: {', '.join(terms[:3])}"

    lines = [f"Code found matching '{', '.join(terms[:3])}':"]
    for fpath, snippets in list(hits.items())[:MAX_SEARCH_HITS]:
        try:
            rel = str(Path(fpath).relative_to(repo_root))
        except ValueError:
            rel = fpath
        lines.append(f"\n`{rel}`")
        for lineno, snippet in snippets:
            lines.append(f"  L{lineno}: {snippet}")
    if len(hits) > MAX_SEARCH_HITS:
        lines.append(f"\n  ...and {len(hits) - MAX_SEARCH_HITS} more files")
    return "\n".join(lines)


# Domain-specific term map: keyword in task → search patterns in code
_DOMAIN_TERMS: dict[str, list[str]] = {
    "razorpay": ["razorpay", "Razorpay", "RazorpayService"],
    "upi": ["upi", "UPI", "vpa", "collect"],
    "qr": ["QRCode", "qr_code", "qrcode", "QrCode"],
    "payment": ["payment", "Payment", "PaymentService"],
    "webhook": ["webhook", "Webhook"],
    "redis": ["redis", "Redis", "RedisService"],
    "docker": ["docker", "Docker", "compose"],
    "auth": ["auth", "Auth", "jwt", "JWT", "guard"],
    "login": ["login", "Login", "signin"],
    "health": ["health", "Health", "healthcheck"],
    "prisma": ["prisma", "Prisma", "PrismaService"],
    "socket": ["socket", "Socket", "WebSocket", "gateway"],
    "order": ["order", "Order", "OrderService"],
    "stripe": ["stripe", "Stripe"],
    "notification": ["notification", "Notification", "fcm", "FCM"],
    "upload": ["upload", "Upload", "multer", "S3"],
    "email": ["email", "Email", "mailer", "smtp"],
    "sms": ["sms", "SMS", "twilio"],
    "cron": ["cron", "Cron", "schedule", "Schedule"],
    "cache": ["cache", "Cache", "CacheService"],
}

# Generic stop words to filter out of free-form task text
_STOP_WORDS = {
    "karo", "debug", "fix", "issue", "problem", "kar", "mein", "hai", "ka",
    "ki", "ko", "se", "me", "ho", "the", "and", "or", "in", "of", "for",
    "to", "a", "an", "with", "please", "help", "check", "look", "find",
    "yeh", "wala", "wali", "this", "that", "is", "are", "was", "were",
}


def _extract_search_terms(task: str) -> list[str]:
    """Extract meaningful code-search terms from the task description."""
    text = task.lower()
    found: list[str] = []

    # 1. Domain-specific map (high priority)
    for keyword, terms in _DOMAIN_TERMS.items():
        if keyword in text:
            found.extend(terms)

    # 2. Generic: extract capitalized/camelCase words from original task that look like identifiers
    words = re.findall(r"\b[A-Z][a-zA-Z]{3,}\b|\b[a-z][a-zA-Z]{3,}[A-Z][a-zA-Z]*\b", task)
    for w in words:
        if w.lower() not in _STOP_WORDS and w not in found:
            found.append(w)

    return list(dict.fromkeys(found))[:7]  # deduplicate, limit


def _find_rg() -> str | None:
    for candidate in (shutil.which("rg"), "/opt/homebrew/bin/rg", "/usr/local/bin/rg"):
        if candidate and Path(candidate).exists():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _project_hint(owner: str, projects: dict) -> str:
    """Return only the relevant project(s) for this owner — generic detection."""
    # Keywords that signal each owner type
    frontend_signals = {"frontend", "web", "app", "ui", "client", "next", "react", "flutter"}
    backend_signals = {"backend", "server", "api", "service", "nest", "go", "django", "fastapi"}

    if owner == "frontend":
        candidates = [p for name, p in projects.items() if name.lower() in frontend_signals]
        if not candidates:
            candidates = [p for name, p in projects.items()
                          if Path(p["path"], "package.json").exists()
                          and Path(p["path"], "pubspec.yaml").exists() is False
                          and "backend" not in name.lower() and "server" not in name.lower()]
        if candidates:
            return "\n".join(f"- `{p['path']}`" for p in candidates[:2])

    if owner == "backend":
        candidates = [p for name, p in projects.items() if name.lower() in backend_signals]
        if not candidates:
            candidates = [p for name, p in projects.items()
                          if Path(p["path"], "package.json").exists()
                          and "frontend" not in name.lower() and "app" not in name.lower()]
        if candidates:
            return "\n".join(f"- `{p['path']}`" for p in candidates[:1])

    # codex / fallback — most specific match first
    if projects:
        return "\n".join(f"- `{p['path']}`" for p in list(projects.values())[:2])
    return "- Inspect repo root."


def _project_summary(projects: dict) -> str:
    if not projects:
        return "- No known project folders detected."
    lines = []
    for name, project in projects.items():
        scripts = sorted(((project.get("package") or {}).get("scripts") or {}).keys())
        hint = ", ".join(scripts[:6]) if scripts else "no package scripts"
        lines.append(f"- `{name}`: `{project.get('path')}` ({hint})")
    return "\n".join(lines)


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "- Use local safe defaults."


def _role_block(role: dict | None) -> str:
    if not role:
        return ""
    return f"\nRole: {role['label']} — {role['prefix']}\n"


def _safe_commands(owner: str, context: dict) -> list[str]:
    projects = context.get("projects", {})
    commands = []
    if owner in {"backend", "codex"} and "backend" in projects:
        scripts = (projects["backend"].get("package") or {}).get("scripts", {})
        if "build" in scripts:
            commands.append("cd backend && npm run build")
        if "test" in scripts:
            commands.append("cd backend && npm run test")
        if Path(projects["backend"]["path"], "docker-compose.yml").exists():
            commands.append("cd backend && docker compose up -d --build")
            commands.append("curl -fsS http://localhost:4000/health/ready")
            commands.append("cd backend && docker compose down")
    if owner in {"frontend", "codex"} and "frontend" in projects:
        scripts = (projects["frontend"].get("package") or {}).get("scripts", {})
        if "lint" in scripts:
            commands.append("cd frontend && npm run lint")
        if "build" in scripts:
            commands.append("cd frontend && npm run build")
    # Flutter projects — discover dynamically
    if owner in {"frontend", "codex"}:
        for name, project in projects.items():
            if Path(project["path"], "pubspec.yaml").exists():
                commands.append(f"cd {name} && flutter analyze")
                commands.append(f"cd {name} && flutter test")
    return commands
