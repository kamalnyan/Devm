"""Repo context collector — generic project detection, no hardcoded folder names."""
from __future__ import annotations

import json
import os
from pathlib import Path


GUIDANCE_NAMES = ("AGENTS.md", "HARNESS.md", "ACCESS-MODEL.md", "RULES.md", "SOUL.md")
MAX_TEXT_CHARS = 5000

_PROJECT_MARKERS = {
    "package.json", "pubspec.yaml", "Cargo.toml", "go.mod",
    "pyproject.toml", "requirements.txt", "build.gradle", "pom.xml", "composer.json",
}
_IGNORE_DIRS = {"node_modules", ".next", "dist", ".git", "__pycache__", ".venv", ".venv-adk", "venv", "build"}


def collect_context(repo_root: Path) -> dict:
    from .ecc import detect_ecc  # avoid circular import

    guidance: dict = {}
    missing: list[str] = []
    warnings: list[str] = []

    for name in GUIDANCE_NAMES:
        matches = _find_files(repo_root, name, max_depth=5, limit=5)
        if matches:
            guidance[name] = [_read_text(path) for path in matches]
        else:
            missing.append(name)

    projects = _detect_projects(repo_root, warnings)
    root_files = _interesting_files(repo_root, max_depth=2)

    # ECC framework detection
    ecc_info = detect_ecc(repo_root)

    return {
        "repo_root": str(repo_root),
        "guidance": guidance,
        "missing_guidance": [m for m in missing if m not in ("RULES.md", "SOUL.md")],
        "warnings": warnings,
        "projects": projects,
        "root_files": root_files,
        "ecc": ecc_info,  # None if not an ECC project
    }


def _detect_projects(repo_root: Path, warnings: list[str]) -> dict:
    projects: dict = {}

    for marker in _PROJECT_MARKERS:
        if (repo_root / marker).exists():
            projects["root"] = _project_context(repo_root)
            break

    for entry in sorted(repo_root.iterdir()):
        if not entry.is_dir() or entry.name in _IGNORE_DIRS or entry.name.startswith("."):
            continue
        for marker in _PROJECT_MARKERS:
            if (entry / marker).exists():
                projects[entry.name] = _project_context(entry)
                break
        else:
            for sub in sorted(entry.iterdir()):
                if not sub.is_dir() or sub.name in _IGNORE_DIRS:
                    continue
                for marker in _PROJECT_MARKERS:
                    if (sub / marker).exists():
                        projects[f"{entry.name}/{sub.name}"] = _project_context(sub)
                        break

    if not projects:
        warnings.append(
            "No project marker files found (package.json, pubspec.yaml, etc.). "
            "Use --repo /path/to/project-root if the app lives elsewhere."
        )

    return projects


def _project_context(project_root: Path) -> dict:
    package = _read_package(project_root / "package.json")
    files = _interesting_files(project_root, max_depth=3)
    readmes = [
        {"path": str(p), "text": _read_text(p)}
        for p in sorted(project_root.glob("README*")) if p.is_file()
    ]
    return {"path": str(project_root), "package": package, "files": files, "readmes": readmes}


def _read_package(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        "name": data.get("name"),
        "scripts": data.get("scripts", {}),
        "dependencies": sorted((data.get("dependencies") or {}).keys()),
        "devDependencies": sorted((data.get("devDependencies") or {}).keys()),
    }


def _interesting_files(root: Path, max_depth: int) -> list[str]:
    names = {
        "Dockerfile", "docker-compose.yml", "docker-compose.yaml", ".dockerignore",
        ".env.example", "next.config.ts", "next.config.js", "tsconfig.json",
        "tsconfig.build.json", "prisma.schema", "pubspec.yaml", "analysis_options.yaml",
    }
    result: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        rel = current.relative_to(root)
        depth = 0 if rel == Path(".") else len(rel.parts)
        dirnames[:] = [n for n in dirnames if n not in _IGNORE_DIRS]
        if depth >= max_depth:
            dirnames[:] = []
        for filename in filenames:
            path = current / filename
            if path.name in names or path.suffix in {".md", ".example"}:
                result.append(str(path))
                if len(result) >= 120:
                    return sorted(result)
    return sorted(result)[:120]


def _read_text(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        text = f"[Could not read: {exc}]"
    return {"path": str(path), "text": text[:MAX_TEXT_CHARS]}


def _find_files(root: Path, filename: str, max_depth: int, limit: int) -> list[Path]:
    matches: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        rel = current.relative_to(root)
        depth = 0 if rel == Path(".") else len(rel.parts)
        dirnames[:] = [n for n in dirnames if n not in _IGNORE_DIRS]
        if depth >= max_depth:
            dirnames[:] = []
        if filename in filenames:
            matches.append(current / filename)
            if len(matches) >= limit:
                break
    return matches
