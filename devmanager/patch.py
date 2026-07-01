"""Patch extractor + applier — reads agent output, finds code changes, applies them.

Supports multiple formats agents use to express file edits:

  1. Unified diff:
       --- a/src/file.ts
       +++ b/src/file.ts
       @@ -10,4 +10,6 @@
       -  old code
       +  new code

  2. Fenced code block with file path header:
       **src/file.ts**
       ```typescript
       entire new file content
       ```

  3. Before/After blocks:
       // before
       old code
       // after
       new code

  4. Create file:
       Create file: src/new-file.ts
       ```typescript
       content
       ```

  5. Edit instruction:
       Edit `src/file.ts` line 52:
       ```
       new content
       ```
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FileChange:
    path: str               # relative file path
    kind: str               # "edit" | "create" | "delete" | "patch"
    content: str = ""       # new full content (for create/edit)
    patch: str = ""         # unified diff text (for patch mode)
    description: str = ""   # what the agent said about this change
    start_line: int = 0     # for partial edits
    end_line: int = 0
    old_snippet: str = ""   # before text (for surgical replace)
    new_snippet: str = ""   # after text


@dataclass
class ApplyResult:
    path: str
    ok: bool
    action: str   # "applied" | "skipped" | "failed" | "created"
    error: str = ""
    backup_path: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Extraction — find code changes in agent output
# ─────────────────────────────────────────────────────────────────────────────

# File extensions we'll write
_CODE_EXTS = {
    "ts", "tsx", "js", "jsx", "mjs", "cjs",
    "py", "go", "rs", "java", "kt", "swift",
    "dart", "yaml", "yml", "json", "toml",
    "sql", "sh", "bash", "zsh",
    "css", "scss", "html", "vue", "svelte",
    "md", "env", "txt",
}

# Regex: unified diff block
_UNIFIED_DIFF_RE = re.compile(
    r"^(---\s+[ab]/\S+\n\+\+\+\s+[ab]/\S+\n(?:@@.+?@@.*\n(?:[ +\-].*\n)*)+)",
    re.M,
)

# Regex: ```lang\ncontent\n``` with file path on line before
_FENCED_WITH_PATH_RE = re.compile(
    r"(?:^|\n)(?:\*{1,2}|`|#+ )?([^\n`*#]+\.(?:" + "|".join(_CODE_EXTS) + r"))[`*\n:]+\s*"
    r"```(?:\w*)\n(.*?)```",
    re.S | re.I,
)

# Regex: "Create file: path.ts" or "Create `path.ts`"
_CREATE_FILE_RE = re.compile(
    r"(?:create|add|new)\s+(?:file|files?)?\s*[:`']?\s*([^\s`'\n]+\.(?:" + "|".join(_CODE_EXTS) + r"))[`']?\s*\n"
    r"```(?:\w*)\n(.*?)```",
    re.S | re.I,
)

# Regex: Before/After block
_BEFORE_AFTER_RE = re.compile(
    r"(?:before|old)[:\s]*\n```(?:\w*)\n(.*?)```\s*\n(?:after|new)[:\s]*\n```(?:\w*)\n(.*?)```",
    re.S | re.I,
)

# Regex: "Edit `path` line N:" followed by code block
_EDIT_INSTRUCTION_RE = re.compile(
    r"[Ee]dit\s+[`'\"]?([^\s`'\"]+\.(?:" + "|".join(_CODE_EXTS) + r"))[`'\"]?(?:\s+(?:at\s+)?line\s+(\d+))?[:\s]*\n"
    r"```(?:\w*)\n(.*?)```",
    re.S,
)


def extract_changes(text: str, repo_root: str) -> list[FileChange]:
    """Extract all file changes from agent output text."""
    changes: list[FileChange] = []
    seen_paths: set[str] = set()

    def _norm(path: str) -> str:
        """Normalize path — strip leading ./ or /"""
        p = path.strip().lstrip("./")
        return p

    def _exists_or_plausible(path: str) -> bool:
        """Only accept paths that exist or look like real project paths."""
        full = Path(repo_root) / path
        if full.exists():
            return True
        # Plausible if contains a known dir segment
        parts = Path(path).parts
        known_dirs = {"src", "lib", "app", "backend", "frontend", "test",
                      "tests", "scripts", "config", "utils", "services",
                      "components", "pages", "api", "models", "migrations"}
        return any(p.lower() in known_dirs for p in parts)

    # 1. Unified diff blocks
    for m in _UNIFIED_DIFF_RE.finditer(text):
        diff_text = m.group(1)
        path_match = re.search(r"---\s+[ab]/(\S+)", diff_text)
        if not path_match:
            continue
        path = _norm(path_match.group(1))
        if path in seen_paths:
            continue
        if _exists_or_plausible(path):
            seen_paths.add(path)
            changes.append(FileChange(
                path=path, kind="patch", patch=diff_text,
                description=f"Unified diff for {path}",
            ))

    # 2. Create file blocks
    for m in _CREATE_FILE_RE.finditer(text):
        path = _norm(m.group(1))
        content = m.group(2).strip()
        if path in seen_paths or not content:
            continue
        seen_paths.add(path)
        changes.append(FileChange(
            path=path, kind="create", content=content,
            description=f"Create new file {path}",
        ))

    # 3. Edit instructions
    for m in _EDIT_INSTRUCTION_RE.finditer(text):
        path = _norm(m.group(1))
        line_num = int(m.group(2)) if m.group(2) else 0
        content = m.group(3).strip()
        if path in seen_paths or not content:
            continue
        if _exists_or_plausible(path):
            seen_paths.add(path)
            changes.append(FileChange(
                path=path, kind="edit", content=content,
                start_line=line_num,
                description=f"Edit {path}" + (f" at line {line_num}" if line_num else ""),
            ))

    # 4. Fenced blocks with file path headers (last resort)
    for m in _FENCED_WITH_PATH_RE.finditer(text):
        path = _norm(m.group(1).strip("* `#\n"))
        content = m.group(2).strip()
        if path in seen_paths or not content:
            continue
        # Only accept if file actually exists (avoid false positives)
        if (Path(repo_root) / path).exists():
            seen_paths.add(path)
            changes.append(FileChange(
                path=path, kind="edit", content=content,
                description=f"Replace content of {path}",
            ))

    return changes


# ─────────────────────────────────────────────────────────────────────────────
# Apply — write changes to disk
# ─────────────────────────────────────────────────────────────────────────────

def apply_change(change: FileChange, repo_root: str, backup: bool = True) -> ApplyResult:
    """Apply a single FileChange to disk."""
    full_path = Path(repo_root) / change.path

    try:
        backup_path = ""
        if backup and full_path.exists():
            backup_path = str(full_path) + ".devm.bak"
            full_path.write_bytes(full_path.read_bytes())
            Path(backup_path).write_bytes(full_path.read_bytes())

        if change.kind == "patch":
            result = _apply_patch(change.patch, repo_root)
            if not result:
                return ApplyResult(change.path, False, "failed", "git apply failed")
            return ApplyResult(change.path, True, "applied", backup_path=backup_path)

        elif change.kind == "create":
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(change.content, encoding="utf-8")
            return ApplyResult(change.path, True, "created")

        elif change.kind in ("edit", "replace"):
            if not full_path.exists():
                return ApplyResult(change.path, False, "failed", f"File not found: {change.path}")

            if change.old_snippet and change.new_snippet:
                # Surgical replace
                original = full_path.read_text(encoding="utf-8")
                if change.old_snippet not in original:
                    return ApplyResult(change.path, False, "failed", "Old snippet not found in file")
                new_content = original.replace(change.old_snippet, change.new_snippet, 1)
                full_path.write_text(new_content, encoding="utf-8")
            elif change.start_line and change.content:
                # Replace specific lines
                lines = full_path.read_text(encoding="utf-8").splitlines(keepends=True)
                end = change.end_line or change.start_line
                new_lines = (
                    lines[:change.start_line - 1]
                    + [change.content + "\n"]
                    + lines[end:]
                )
                full_path.write_text("".join(new_lines), encoding="utf-8")
            else:
                # Full file replace
                full_path.write_text(change.content, encoding="utf-8")

            return ApplyResult(change.path, True, "applied", backup_path=backup_path)

        return ApplyResult(change.path, False, "failed", f"Unknown kind: {change.kind}")

    except Exception as exc:
        return ApplyResult(change.path, False, "failed", str(exc))


def _apply_patch(patch_text: str, repo_root: str) -> bool:
    """Try git apply first, fallback to patch command."""
    try:
        result = subprocess.run(
            ["git", "apply", "--check", "-"],
            input=patch_text.encode(), cwd=repo_root,
            capture_output=True, timeout=15,
        )
        if result.returncode == 0:
            subprocess.run(
                ["git", "apply", "-"],
                input=patch_text.encode(), cwd=repo_root,
                capture_output=True, timeout=15,
            )
            return True
    except Exception:
        pass
    return False


def restore_backup(change_path: str, repo_root: str) -> bool:
    """Restore a backed-up file."""
    bak = Path(repo_root) / (change_path + ".devm.bak")
    orig = Path(repo_root) / change_path
    if bak.exists():
        orig.write_bytes(bak.read_bytes())
        bak.unlink()
        return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Diff display — colored terminal diff
# ─────────────────────────────────────────────────────────────────────────────

def show_diff(change: FileChange, repo_root: str) -> None:
    """Show a colored diff of what will change."""
    RED    = "\033[31m"
    GREEN  = "\033[32m"
    CYAN   = "\033[36m"
    DIM    = "\033[2m"
    BOLD   = "\033[1m"
    RESET  = "\033[0m"

    def _sep(char: str = "·", width: int = 60) -> None:
        print(f"  {DIM}{char * width}{RESET}")

    full_path = Path(repo_root) / change.path
    print(f"\n  {BOLD}{CYAN}📄 {change.path}{RESET}  {DIM}({change.kind}){RESET}")
    _sep()

    if change.kind == "patch" and change.patch:
        for line in change.patch.splitlines():
            if line.startswith("+++") or line.startswith("---"):
                print(f"  {DIM}{line}{RESET}")
            elif line.startswith("@@"):
                print(f"  {CYAN}{line}{RESET}")
            elif line.startswith("+"):
                print(f"  {GREEN}{line}{RESET}")
            elif line.startswith("-"):
                print(f"  {RED}{line}{RESET}")
            else:
                print(f"  {DIM}{line}{RESET}")

    elif change.kind == "create":
        print(f"  {GREEN}+ (new file){RESET}")
        for line in change.content.splitlines()[:30]:
            print(f"  {GREEN}+ {line}{RESET}")
        if len(change.content.splitlines()) > 30:
            print(f"  {DIM}  ... ({len(change.content.splitlines())} lines total){RESET}")

    elif change.kind in ("edit", "replace"):
        if full_path.exists():
            original_lines = full_path.read_text(encoding="utf-8").splitlines()
            new_lines = change.content.splitlines()
            print(f"  {DIM}Before: {len(original_lines)} lines → After: {len(new_lines)} lines{RESET}")
            for line in new_lines[:20]:
                print(f"  {GREEN}+ {line}{RESET}")
            if len(new_lines) > 20:
                print(f"  {DIM}  ... ({len(new_lines)} lines total){RESET}")
        else:
            print(f"  {GREEN}+ (new file, {len(change.content.splitlines())} lines){RESET}")

    _sep()
