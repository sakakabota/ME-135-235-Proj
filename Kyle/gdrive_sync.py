"""
gdrive_sync.py — Google Docs Feature Report Writer
====================================================
Syncs ME135 documentation to Google Drive (school account).

Each feature gets ONE Google Doc that grows over time:
  "ME135 | Computer Vision Pipeline"
  "ME135 | Serial Communication Protocol"
  "ME135 | ESP32 Firmware"
  "ME135 | System Integration"
  "ME135 | Hardware & Platform"
  "ME135 | Documentation System"

Every commit appends a new versioned section to the relevant doc(s).
By end of project: a clear per-feature evolution log lives in Drive.

Setup:
    python gdrive_setup.py       # one-time OAuth (opens browser)
    GDRIVE_FOLDER_ID=...         # optional: target specific Drive folder

Credentials:
    Place gdrive_credentials.json in Kyle/ (from Google Cloud Console).
    Token auto-saved to Kyle/.gdrive_token.json after first auth.
"""

import json
import os
import re
from pathlib import Path
from datetime import datetime

# Google API imports (installed via pip)
try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    GOOGLE_AVAILABLE = True
except ImportError:
    GOOGLE_AVAILABLE = False

# ─── Feature Map ──────────────────────────────────────────────────────────────

# Maps filenames → feature doc title
FEATURE_MAP: dict[str, str] = {
    "cv_pipeline.py":           "Computer Vision Pipeline",
    "gpu_accelerated.py":       "Computer Vision Pipeline",
    "serial_protocol.py":       "Serial Communication Protocol",
    "PROTOCOL_SPEC.md":         "Serial Communication Protocol",
    "esp32_main.cpp":           "ESP32 Firmware",
    "platformio.ini":           "ESP32 Firmware",
    "main.py":                  "System Integration",
    "config.yaml":              "System Integration",
    "PROJECT_README.md":        "System Integration",
    "hardware_recommendation.md": "Hardware & Platform",
    "SETUP.md":                 "Hardware & Platform",
    "requirements.txt":         "Hardware & Platform",
    "orchestrator.py":          "Agent Swarm",
    "doc_agent.py":             "Documentation System",
    "gdrive_sync.py":           "Documentation System",
    "gdrive_setup.py":          "Documentation System",
}

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
]

CREDS_PATH  = Path(__file__).parent / "gdrive_credentials.json"
TOKEN_PATH  = Path(__file__).parent / ".gdrive_token.json"
FOLDER_NAME = "ME135 Feature Reports"


# ─── Feature Detection ────────────────────────────────────────────────────────

def detect_features(changed_files: list[str]) -> list[str]:
    """
    Given a list of changed file paths (relative to repo root),
    return the unique feature names that were touched.
    Falls back to ["General"] if no mapping found.
    """
    features: set[str] = set()
    for filepath in changed_files:
        basename = Path(filepath).name
        feature = FEATURE_MAP.get(basename)
        if feature:
            features.add(feature)

    return sorted(features) if features else ["General"]


def get_changed_files(repo_root: Path) -> list[str]:
    """Return files changed in HEAD relative to HEAD~1, within Kyle/."""
    import subprocess
    result = subprocess.run(
        ["git", "diff", "HEAD~1", "--name-only", "--", "Kyle/"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    files = [f.strip() for f in result.stdout.strip().splitlines() if f.strip()]
    if not files:
        # First commit — list all tracked files in Kyle/
        result = subprocess.run(
            ["git", "show", "--name-only", "--format=", "HEAD", "--", "Kyle/"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
        )
        files = [f.strip() for f in result.stdout.strip().splitlines() if f.strip()]
    return files


# ─── Google Auth ──────────────────────────────────────────────────────────────

def get_credentials() -> "Credentials | None":
    """Load or refresh OAuth credentials. Returns None if auth not set up."""
    if not GOOGLE_AVAILABLE:
        print("  ⚠️  google-api-python-client not installed — skipping Drive sync.")
        return None

    if not CREDS_PATH.exists():
        print(f"  ⚠️  {CREDS_PATH.name} not found — run `python gdrive_setup.py` to set up Google Drive sync.")
        return None

    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_PATH), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_PATH.write_text(creds.to_json())

    return creds


# ─── Google Drive / Docs helpers ──────────────────────────────────────────────

def get_or_create_folder(drive_service, folder_name: str, parent_id: str | None = None) -> str:
    """Find or create a Drive folder. Returns folder ID."""
    query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        query += f" and '{parent_id}' in parents"

    results = drive_service.files().list(q=query, fields="files(id, name)").execute()
    items = results.get("files", [])
    if items:
        return items[0]["id"]

    # Create
    meta = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        meta["parents"] = [parent_id]

    folder = drive_service.files().create(body=meta, fields="id").execute()
    return folder["id"]


def get_or_create_doc(drive_service, docs_service, title: str, folder_id: str) -> tuple[str, bool]:
    """
    Find or create a Google Doc with the given title in folder_id.
    Returns (doc_id, is_new).
    """
    query = (
        f"name='{title}' and "
        f"mimeType='application/vnd.google-apps.document' and "
        f"trashed=false and '{folder_id}' in parents"
    )
    results = drive_service.files().list(q=query, fields="files(id, name)").execute()
    items = results.get("files", [])

    if items:
        return items[0]["id"], False

    # Create new doc
    meta = {
        "name": title,
        "mimeType": "application/vnd.google-apps.document",
        "parents": [folder_id],
    }
    doc_file = drive_service.files().create(body=meta, fields="id").execute()
    doc_id = doc_file["id"]

    # Initialize with title heading
    docs_service.documents().batchUpdate(
        documentId=doc_id,
        body={
            "requests": [
                {
                    "insertText": {
                        "location": {"index": 1},
                        "text": f"{title}\n\n",
                    }
                },
                {
                    "updateParagraphStyle": {
                        "range": {"startIndex": 1, "endIndex": len(title) + 1},
                        "paragraphStyle": {"namedStyleType": "HEADING_1"},
                        "fields": "namedStyleType",
                    }
                },
            ]
        },
    ).execute()

    return doc_id, True


def get_doc_version(docs_service, doc_id: str) -> int:
    """Count existing version sections (## v{n}) in the doc."""
    doc = docs_service.documents().get(documentId=doc_id).execute()
    full_text = ""
    for elem in doc.get("body", {}).get("content", []):
        for part in elem.get("paragraph", {}).get("elements", []):
            full_text += part.get("textRun", {}).get("content", "")

    return len(re.findall(r"^## v\d+", full_text, re.MULTILINE))


def get_doc_end_index(docs_service, doc_id: str) -> int:
    """Return the index of the last character in the document body."""
    doc = docs_service.documents().get(documentId=doc_id).execute()
    content = doc.get("body", {}).get("content", [])
    if not content:
        return 1
    last = content[-1]
    return last.get("endIndex", 1) - 1


def append_section_to_doc(docs_service, doc_id: str, text: str):
    """
    Append text to the end of a Google Doc.
    Uses a heading style for lines starting with ## or ###.
    """
    end = get_doc_end_index(docs_service, doc_id)

    requests = []
    cursor = end

    # Insert all text at once, then apply styles paragraph by paragraph
    full_text = "\n" + text.strip() + "\n\n"
    requests.append({
        "insertText": {
            "location": {"index": cursor},
            "text": full_text,
        }
    })

    # Apply heading styles to ## and ### lines
    pos = cursor + 1  # +1 for the leading \n
    for line in full_text[1:].split("\n"):  # skip the leading \n
        line_end = pos + len(line)
        if line.startswith("## "):
            requests.append({
                "updateParagraphStyle": {
                    "range": {"startIndex": pos, "endIndex": line_end},
                    "paragraphStyle": {"namedStyleType": "HEADING_2"},
                    "fields": "namedStyleType",
                }
            })
        elif line.startswith("### "):
            requests.append({
                "updateParagraphStyle": {
                    "range": {"startIndex": pos, "endIndex": line_end},
                    "paragraphStyle": {"namedStyleType": "HEADING_3"},
                    "fields": "namedStyleType",
                }
            })
        pos = line_end + 1  # +1 for the \n separator

    docs_service.documents().batchUpdate(
        documentId=doc_id,
        body={"requests": requests},
    ).execute()


# ─── Section Composer ─────────────────────────────────────────────────────────

def compose_feature_section(
    feature: str,
    version: int,
    commit_sha: str,
    report_sections: list[tuple[str, str]],
    proposals: list[dict],
    feature_files: set[str],
) -> str:
    """
    Build the Markdown text for one versioned entry in a feature's Google Doc.
    Filters proposals to only those affecting files in this feature.
    """
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"## v{version} — {date_str} — commit `{commit_sha}`",
        "",
    ]

    # Include all report sections (historian + architect cover cross-feature context)
    section_order = ["What Changed", "Evolution Timeline", "System Architecture",
                     "Data Flow", "Module Dependency Graph", "Code Health Summary"]

    section_map = {title.strip(): body for title, body in report_sections}

    def find(name):
        if name in section_map:
            return section_map[name]
        for k, v in section_map.items():
            if name.lower() in k.lower():
                return v
        return None

    for name in section_order:
        body = find(name)
        if body:
            lines.append(f"### {name}")
            lines.append("")
            lines.append(body.strip())
            lines.append("")

    # Filter proposals to this feature's files
    feature_proposals = [
        p for p in proposals
        if any(Path(f).name in feature_files for f in p.get("affected_files", []))
    ]

    if feature_proposals:
        lines.append("### Improvement Proposals")
        lines.append("")
        for p in feature_proposals:
            risk_icon = {"low": "🟢", "medium": "🟡", "high": "🔴"}.get(p["risk_level"], "⚪")
            lines.append(f"**[{p['id']}] {p['title']}** — {risk_icon} {p['risk_level']} risk — {p['priority']}")
            lines.append("")
            lines.append(f"Problem: {p['problem']}")
            lines.append("")
            lines.append(f"Fix: {p['proposed_fix']}")
            lines.append("")

    lines.append("---")
    return "\n".join(lines)


# ─── Main Entry Point ─────────────────────────────────────────────────────────

def sync_to_drive(
    report_sections: list[tuple[str, str]],
    proposals: list[dict],
    commit_sha: str,
    repo_root: Path,
    folder_id_override: str | None = None,
) -> dict[str, str]:
    """
    Sync the current report to Google Drive feature docs.

    Returns a dict of {feature_name: google_doc_url} for all updated docs.
    Returns {} if Drive sync is not configured.
    """
    creds = get_credentials()
    if not creds:
        return {}

    try:
        drive_service = build("drive", "v3", credentials=creds)
        docs_service  = build("docs", "v1", credentials=creds)
    except Exception as e:
        print(f"  ⚠️  Google API error: {e}")
        return {}

    # Detect which features this commit touches
    changed_files = get_changed_files(repo_root)
    features      = detect_features(changed_files)

    print(f"\n  📁 Drive sync → features: {', '.join(features)}")

    # Get/create the root folder
    folder_id = folder_id_override or os.environ.get("GDRIVE_FOLDER_ID")
    if not folder_id:
        folder_id = get_or_create_folder(drive_service, FOLDER_NAME)
        print(f"  📂 Folder: {FOLDER_NAME} ({folder_id})")

    updated_docs: dict[str, str] = {}

    for feature in features:
        doc_title = f"ME135 | {feature}"

        # Get feature's files set for filtering proposals
        feature_files = {
            fname for fname, feat in FEATURE_MAP.items() if feat == feature
        }

        try:
            doc_id, is_new = get_or_create_doc(
                drive_service, docs_service, doc_title, folder_id
            )
            version = get_doc_version(docs_service, doc_id) + 1

            section_text = compose_feature_section(
                feature, version, commit_sha,
                report_sections, proposals, feature_files,
            )

            append_section_to_doc(docs_service, doc_id, section_text)

            doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"
            updated_docs[feature] = doc_url
            status = "✨ created" if is_new else f"📝 v{version} appended"
            print(f"  {status}: {doc_title}")
            print(f"    → {doc_url}")

        except HttpError as e:
            print(f"  ⚠️  Failed to update '{doc_title}': {e}")

    return updated_docs
