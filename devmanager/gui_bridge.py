from __future__ import annotations

import subprocess
from pathlib import Path

from devmanager.agent_config import allowed_gui_apps


def send_to_gui_app(app_name: str, prompt: str, submit: bool = False) -> dict:
    app = _resolve_app(app_name)
    if not app:
        allowed = ", ".join(sorted(allowed_gui_apps().values()))
        return {
            "ok": False,
            "app": app_name,
            "message": f"Blocked. Allowed apps from config: {allowed}.",
        }

    app_path = Path("/Applications") / f"{app}.app"
    if not app_path.exists():
        return {"ok": False, "app": app, "message": f"App not found: {app_path}"}

    activate = subprocess.run(
        ["osascript", "-e", f'tell application "{app}" to activate'],
        text=True,
        capture_output=True,
        check=False,
    )
    if activate.returncode != 0:
        return {"ok": False, "app": app, "message": activate.stderr.strip() or activate.stdout.strip()}

    clipboard = subprocess.run(
        ["pbcopy"],
        input=prompt,
        text=True,
        capture_output=True,
        check=False,
    )
    if clipboard.returncode != 0:
        return {"ok": False, "app": app, "message": clipboard.stderr.strip() or clipboard.stdout.strip()}

    script = _paste_script(submit)
    pasted = subprocess.run(
        ["osascript", "-e", script],
        text=True,
        capture_output=True,
        check=False,
    )
    if pasted.returncode != 0:
        # FIX 4: Accessibility blocked — clipboard already has the prompt, tell user to paste manually
        err = pasted.stderr.strip() or pasted.stdout.strip()
        print(f"\n⚠  Auto-paste blocked (macOS Accessibility not granted for this terminal).")
        print(f"   Prompt is already in your clipboard — open {app} and press ⌘V to paste.")
        print(f"   To enable auto-paste permanently:")
        print(f"   System Settings → Privacy & Security → Accessibility → add your Terminal app")
        return {
            "ok": "clipboard",
            "app": app,
            "message": "Prompt copied to clipboard. Paste manually with ⌘V.",
            "clipboard": True,
            "accessibility_error": err,
        }

    return {
        "ok": True,
        "app": app,
        "submitted": submit,
        "message": "Prompt pasted into GUI app." + (" Submitted with Return." if submit else ""),
    }


def _resolve_app(app_name: str) -> str | None:
    key = app_name.strip().lower()
    apps = allowed_gui_apps()
    return apps.get(key)


def _paste_script(submit: bool) -> str:
    submit_line = 'key code 36' if submit else ''
    return f'''
tell application "System Events"
  delay 0.5
  keystroke "v" using command down
  delay 0.2
  {submit_line}
end tell
'''
