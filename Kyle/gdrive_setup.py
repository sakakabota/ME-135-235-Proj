#!/usr/bin/env python3
"""
gdrive_setup.py — One-Time Google Drive Setup via rclone
=========================================================
No Google Cloud Console admin access required.
rclone bundles its own OAuth app — just sign in with your school account.

Run once:
    python gdrive_setup.py

What it does:
  1. Verifies rclone is installed
  2. Runs `rclone config create` to set up Google Drive remote named "me135drive"
  3. Opens browser → sign in with your @berkeley.edu account
  4. Creates the "ME135 Feature Reports" folder in your Drive
  5. Does a test sync to verify everything works
"""

import subprocess
import sys
from pathlib import Path

REMOTE_NAME   = "me135drive"
DRIVE_FOLDER  = "ME135 Feature Reports"
FEATURES_DIR  = Path(__file__).parent / "docs" / "features"


def run(cmd: list[str], check=True, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, **kwargs)


def main():
    print()
    print("╔══════════════════════════════════════════════════╗")
    print("║   ME135 Google Drive Setup (via rclone)          ║")
    print("║   No Google Cloud Console admin required.        ║")
    print("╚══════════════════════════════════════════════════╝")
    print()

    # ── 1. Check rclone ───────────────────────────────────────────────────────
    result = run(["which", "rclone"], check=False, capture_output=True)
    if result.returncode != 0:
        print("ERROR: rclone not found.")
        print("Install it with:  brew install rclone")
        sys.exit(1)

    version = run(["rclone", "version", "--check=false"],
                  capture_output=True, text=True, check=False)
    print(f"  ✅ rclone found: {version.stdout.splitlines()[0] if version.stdout else 'ok'}")

    # ── 2. Check if remote already exists ────────────────────────────────────
    remotes = run(["rclone", "listremotes"], capture_output=True, text=True, check=False)
    if f"{REMOTE_NAME}:" in remotes.stdout:
        print(f"  ✅ Remote '{REMOTE_NAME}' already configured.")
    else:
        print(f"\n  Configuring Google Drive remote '{REMOTE_NAME}'…")
        print("  A browser window will open — sign in with your @berkeley.edu account.")
        print("  When asked for a name, it will use 'me135drive' automatically.\n")

        # rclone config create runs non-interactively for most fields,
        # but needs browser for OAuth. Use `rclone config` interactively instead.
        print("  Running: rclone config")
        print("  ─────────────────────────────────────────────────────────────")
        print("  Follow these steps in the config wizard:")
        print("    1. Type 'n' → new remote")
        print(f"   2. Name: {REMOTE_NAME}")
        print("    3. Storage: choose 'drive' (Google Drive) — type its number")
        print("    4. client_id: leave blank (press Enter)")
        print("    5. client_secret: leave blank (press Enter)")
        print("    6. scope: type '1' (full access)")
        print("    7. root_folder_id: leave blank")
        print("    8. service_account_file: leave blank")
        print("    9. Edit advanced config? n")
        print("   10. Use auto config? y  → browser opens, sign in with school account")
        print("   11. Configure as shared drive? n")
        print("   12. Keep this? y")
        print("   13. Type 'q' to quit config")
        print("  ─────────────────────────────────────────────────────────────\n")
        run(["rclone", "config"])

    # ── 3. Verify remote works ────────────────────────────────────────────────
    result = run(
        ["rclone", "lsd", f"{REMOTE_NAME}:"],
        capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        print(f"\nERROR: Could not connect to remote '{REMOTE_NAME}'.")
        print(f"  {result.stderr.strip()}")
        sys.exit(1)

    email_hint = ""
    # Try to get user info
    info = run(
        ["rclone", "about", f"{REMOTE_NAME}:"],
        capture_output=True, text=True, check=False
    )
    if info.returncode == 0:
        print(f"  ✅ Connected to Google Drive:")
        for line in info.stdout.splitlines()[:4]:
            print(f"     {line}")
    else:
        print(f"  ✅ Remote '{REMOTE_NAME}' connected.")

    # ── 4. Create destination folder ─────────────────────────────────────────
    print(f"\n  Creating folder '{DRIVE_FOLDER}' in Drive…")
    run(
        ["rclone", "mkdir", f"{REMOTE_NAME}:{DRIVE_FOLDER}"],
        check=False
    )

    # ── 5. Write a test file and sync ────────────────────────────────────────
    FEATURES_DIR.mkdir(parents=True, exist_ok=True)
    test_file = FEATURES_DIR / "ME135_SETUP_TEST.md"
    test_file.write_text(
        "# ME135 Feature Reports\n\nGoogle Drive sync is working.\n"
        "This file will be replaced by real feature reports on your next git push.\n",
        encoding="utf-8",
    )

    print(f"  Syncing test file to Drive…")
    result = run(
        ["rclone", "sync", str(FEATURES_DIR), f"{REMOTE_NAME}:{DRIVE_FOLDER}",
         "--drive-skip-gdocs", "--log-level", "ERROR"],
        capture_output=True, text=True, check=False,
    )

    if result.returncode == 0:
        folder_url = f"https://drive.google.com/drive/search?q={DRIVE_FOLDER.replace(' ', '+')}"
        print(f"\n  ✅ Setup complete!")
        print(f"  📂 Feature Reports folder in your Drive:")
        print(f"     Search Drive for: {DRIVE_FOLDER}")
        print()
        print("  From now on, every `git push` will:")
        print("    1. Run 3 AI agents (Historian, Architect, Critic)")
        print("    2. Append a versioned section to each affected feature doc")
        print("    3. Sync all feature docs to your Drive automatically")
        print()
    else:
        print(f"  ⚠️  Sync failed: {result.stderr[:200]}")
        print("  Feature docs will still be saved locally in Kyle/docs/features/")

    # Clean up test file
    test_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
