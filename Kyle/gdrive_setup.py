#!/usr/bin/env python3
"""
gdrive_setup.py — One-Time Google Drive Setup via rclone
=========================================================
No Google Cloud Console admin access required.
rclone bundles its own OAuth app — just sign in with your school account.

Run once:
    python3 gdrive_setup.py

What it does:
  1. Verifies rclone is installed
  2. Creates Google Drive remote named "me135drive" non-interactively
  3. Opens browser → sign in with your @berkeley.edu account
  4. Creates the "ME135 Feature Reports" folder in your Drive
  5. Does a test sync to verify everything works
"""

import shutil
import subprocess
import sys
from pathlib import Path

REMOTE_NAME  = "me135drive"
DRIVE_FOLDER = "ME135 Feature Reports"
FEATURES_DIR = Path(__file__).parent / "docs" / "features"


def run(cmd: list[str], check=True, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, **kwargs)


def _create_remote():
    print(f"  ⚙️  Creating '{REMOTE_NAME}' remote with Drive scope…")
    result = run(
        ["rclone", "config", "create", REMOTE_NAME, "drive",
         "scope", "drive",
         "client_id", "",
         "client_secret", ""],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        print(f"  ⚠️  Config create warning: {result.stderr.strip()[:200]}")


def main():
    print()
    print("╔══════════════════════════════════════════════════╗")
    print("║   ME135 Google Drive Setup (via rclone)          ║")
    print("╚══════════════════════════════════════════════════╝")
    print()

    # ── 1. Check rclone ───────────────────────────────────────────────────────
    if shutil.which("rclone") is None:
        print("ERROR: rclone not found.  Install: brew install rclone")
        sys.exit(1)

    ver = run(["rclone", "version"], capture_output=True, text=True, check=False)
    print(f"  ✅ {ver.stdout.splitlines()[0] if ver.stdout else 'rclone ok'}")

    # ── 2. Check existing config or recreate ──────────────────────────────────
    remotes = run(["rclone", "listremotes"], capture_output=True, text=True, check=False)
    needs_oauth = True
    if f"{REMOTE_NAME}:" in remotes.stdout:
        # Test if the existing remote is functional
        test = run(["rclone", "lsd", f"{REMOTE_NAME}:"], capture_output=True, check=False)
        if test.returncode == 0:
            print(f"  ✅ Remote '{REMOTE_NAME}' already configured and working — skipping setup.")
            needs_oauth = False
        else:
            print(f"  🔄 Remote '{REMOTE_NAME}' exists but auth failed — removing and recreating…")
            run(["rclone", "config", "delete", REMOTE_NAME], check=False, capture_output=True)
            _create_remote()
    else:
        _create_remote()

    # ── 3. Trigger OAuth browser flow (only if remote was just created) ───────
    if needs_oauth:
        print()
        print("  🌐 Opening browser for Google sign-in…")
        print("  → Sign in with kyle-nelson@berkeley.edu")
        print("  → Click 'Allow' when asked about rclone's Drive access")
        print()

        # rclone config reconnect handles the OAuth interactively
        result = run(
            ["rclone", "config", "reconnect", REMOTE_NAME, "--auto-confirm"],
            check=False,
        )
        if result.returncode != 0:
            # auto-confirm may not be available on older rclone; fall back
            run(["rclone", "config", "reconnect", REMOTE_NAME], check=False)

    # ── 4. Verify connection ──────────────────────────────────────────────────
    print()
    result = run(["rclone", "lsd", f"{REMOTE_NAME}:"], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(f"  ❌ Connection failed: {result.stderr.strip()[:300]}")
        print()
        print("  Troubleshooting:")
        print("  1. Make sure you clicked 'Allow' in the browser")
        print("  2. Try again: python3 gdrive_setup.py")
        sys.exit(1)

    info = run(["rclone", "about", f"{REMOTE_NAME}:"], capture_output=True, text=True, check=False)
    if info.returncode == 0:
        print("  ✅ Connected to Google Drive:")
        for line in info.stdout.splitlines()[:3]:
            print(f"     {line}")

    # ── 5. Create folder + test sync ─────────────────────────────────────────
    print(f"\n  📂 Creating '{DRIVE_FOLDER}' folder in Drive…")
    run(["rclone", "mkdir", f"{REMOTE_NAME}:{DRIVE_FOLDER}"], check=False, capture_output=True)

    FEATURES_DIR.mkdir(parents=True, exist_ok=True)
    test_file = FEATURES_DIR / "_setup_test.md"
    test_file.write_text("# ME135 Feature Reports\nDrive sync is working.\n")

    result = run(
        ["rclone", "sync", str(FEATURES_DIR), f"{REMOTE_NAME}:{DRIVE_FOLDER}",
         "--drive-skip-gdocs", "--log-level", "ERROR"],
        capture_output=True, text=True, check=False,
    )
    test_file.unlink(missing_ok=True)

    if result.returncode == 0:
        print()
        print("  ✅ Setup complete!")
        print(f"  📂 Search Drive for:  {DRIVE_FOLDER}")
        print()
        print("  Every git push will now:")
        print("    1. Run 3 AI agents (Historian, Architect, Critic)")
        print("    2. Append a versioned section to each affected feature doc")
        print("    3. Sync all feature docs to your Drive automatically")
        print()
    else:
        print(f"  ⚠️  Test sync failed: {result.stderr[:200]}")
        print("  Feature docs will still be saved locally in Kyle/docs/features/")


if __name__ == "__main__":
    main()
