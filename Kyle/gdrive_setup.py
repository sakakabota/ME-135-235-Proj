#!/usr/bin/env python3
"""
gdrive_setup.py — One-Time Google Drive OAuth Setup
=====================================================
Run this ONCE to authorize Google Drive + Docs access for your school account.
Token is saved to .gdrive_token.json and reused automatically.

Steps:
  1. Go to https://console.cloud.google.com/
  2. Create a project → Enable "Google Drive API" + "Google Docs API"
  3. Credentials → OAuth 2.0 Client ID → Desktop app → Download JSON
  4. Save the downloaded file as:  Kyle/gdrive_credentials.json
  5. Run: python gdrive_setup.py
  6. Sign in with your school (@berkeley.edu) Google account in the browser

After this, doc_agent.py will auto-sync feature reports to Drive on every push.
"""

from pathlib import Path
import sys

CREDS_PATH = Path(__file__).parent / "gdrive_credentials.json"
TOKEN_PATH = Path(__file__).parent / ".gdrive_token.json"

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
]

def main():
    print()
    print("╔═══════════════════════════════════════════════════╗")
    print("║        ME135 Google Drive Setup                   ║")
    print("╚═══════════════════════════════════════════════════╝")
    print()

    if not CREDS_PATH.exists():
        print("ERROR: gdrive_credentials.json not found.")
        print()
        print("To get it:")
        print("  1. https://console.cloud.google.com/")
        print("  2. Create project → Enable 'Google Drive API' + 'Google Docs API'")
        print("  3. Credentials → OAuth 2.0 Client ID → Desktop app → Download JSON")
        print(f"  4. Save as: {CREDS_PATH}")
        print("  5. Re-run this script")
        sys.exit(1)

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        print("ERROR: Google libraries not installed.")
        print("Run: pip install google-api-python-client google-auth-oauthlib")
        sys.exit(1)

    print("  Opening browser for Google account sign-in…")
    print("  → Sign in with your school (@berkeley.edu) account")
    print()

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_PATH), SCOPES)
    creds = flow.run_local_server(port=0)
    TOKEN_PATH.write_text(creds.to_json())
    print(f"  ✅ Token saved to {TOKEN_PATH.name}")

    # Quick sanity check — list Drive root
    drive_service = build("drive", "v3", credentials=creds)
    about = drive_service.about().get(fields="user").execute()
    user_email = about.get("user", {}).get("emailAddress", "unknown")
    print(f"  ✅ Authenticated as: {user_email}")

    # Create the folder now so the URL is ready
    from gdrive_sync import get_or_create_folder, FOLDER_NAME
    folder_id = get_or_create_folder(drive_service, FOLDER_NAME)
    folder_url = f"https://drive.google.com/drive/folders/{folder_id}"
    print(f"  📂 Feature Reports folder: {folder_url}")
    print()
    print("  Setup complete! Feature reports will auto-sync on every git push.")
    print()


if __name__ == "__main__":
    main()
