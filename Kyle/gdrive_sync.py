"""
gdrive_sync.py — Feature Report Writer + Google Drive Sync
===========================================================
Maintains one Markdown file per project feature, appending a versioned
section on every commit. Syncs to Google Drive via rclone (no Cloud Console
admin access required — rclone bundles its own OAuth app).

Feature docs live locally in:
    Kyle/docs/features/ME135_Computer_Vision_Pipeline.md
    Kyle/docs/features/ME135_Serial_Communication_Protocol.md
    ...

After every run, rclone syncs them to:
    Google Drive → ME135 Feature Reports/

One-time setup (run once):
    rclone config     # choose Google Drive, sign in with school account
                      # name the remote "me135drive" when prompted

Then set the remote in env or rclone_remote below.
"""

import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

# ─── Config ───────────────────────────────────────────────────────────────────

DOCS_DIR     = Path(__file__).parent / "docs"
FEATURES_DIR = DOCS_DIR / "features"
FEATURES_DIR.mkdir(parents=True, exist_ok=True)

REPO_ROOT = Path(__file__).parent.parent

# rclone remote name (set once via `rclone config`, name it "me135drive")
# Override with env var RCLONE_REMOTE if you named it differently.
RCLONE_REMOTE      = os.environ.get("RCLONE_REMOTE", "me135drive")
RCLONE_DRIVE_FOLDER = os.environ.get("RCLONE_DRIVE_FOLDER", "ME135 Feature Reports")

# ─── Feature Map ──────────────────────────────────────────────────────────────

FEATURE_MAP: dict[str, str] = {
    "cv_pipeline.py":             "Computer Vision Pipeline",
    "gpu_accelerated.py":         "Computer Vision Pipeline",
    "serial_protocol.py":         "Serial Communication Protocol",
    "PROTOCOL_SPEC.md":           "Serial Communication Protocol",
    "esp32_main.cpp":             "ESP32 Firmware",
    "platformio.ini":             "ESP32 Firmware",
    "main.py":                    "System Integration",
    "config.yaml":                "System Integration",
    "PROJECT_README.md":          "System Integration",
    "hardware_recommendation.md": "Hardware & Platform",
    "SETUP.md":                   "Hardware & Platform",
    "requirements.txt":           "Hardware & Platform",
    "orchestrator.py":            "Agent Swarm",
    "doc_agent.py":               "Documentation System",
    "gdrive_sync.py":             "Documentation System",
    "gdrive_setup.py":            "Documentation System",
}


def feature_to_filename(feature: str) -> str:
    """'Computer Vision Pipeline' → 'ME135_Computer_Vision_Pipeline.md'"""
    slug = feature.replace(" ", "_").replace("/", "_")
    return f"ME135_{slug}.md"


# ─── Feature Detection ────────────────────────────────────────────────────────

def detect_features(changed_files: list[str]) -> list[str]:
    """Map changed file paths to their feature names."""
    features: set[str] = set()
    for filepath in changed_files:
        basename = Path(filepath).name
        feature = FEATURE_MAP.get(basename)
        if feature:
            features.add(feature)
    return sorted(features) if features else ["General"]


def get_changed_files(repo_root: Path) -> list[str]:
    """Files changed in HEAD vs HEAD~1 inside Kyle/."""
    result = subprocess.run(
        ["git", "diff", "HEAD~1", "--name-only", "--", "Kyle/"],
        cwd=str(repo_root), capture_output=True, text=True,
    )
    files = [f.strip() for f in result.stdout.strip().splitlines() if f.strip()]
    if not files:
        # First commit: list all tracked files
        result = subprocess.run(
            ["git", "show", "--name-only", "--format=", "HEAD", "--", "Kyle/"],
            cwd=str(repo_root), capture_output=True, text=True,
        )
        files = [f.strip() for f in result.stdout.strip().splitlines() if f.strip()]
    return files


# ─── Local Feature Doc Writer ─────────────────────────────────────────────────

def get_feature_version(feature_path: Path) -> int:
    """Count ## v{n} headers in existing doc to determine next version."""
    if not feature_path.exists():
        return 0
    content = feature_path.read_text(encoding="utf-8")
    return len(re.findall(r"^## v\d+", content, re.MULTILINE))


def ensure_feature_doc(feature_path: Path, feature: str):
    """Create the feature doc with a header if it doesn't exist yet."""
    if feature_path.exists():
        return
    header = f"# ME135 | {feature}\n\n"
    header += f"> Evolution log — one section per commit.\n\n"
    header += f"---\n\n"
    feature_path.write_text(header, encoding="utf-8")


def compose_feature_section(
    feature: str,
    version: int,
    commit_sha: str,
    report_sections: list[tuple[str, str]],
    proposals: list[dict],
) -> str:
    """Build the markdown text for one versioned commit entry."""
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    feature_files = {fname for fname, feat in FEATURE_MAP.items() if feat == feature}

    lines = [
        f"## v{version} — {date_str} — `{commit_sha}`",
        "",
    ]

    # Sections in display order
    section_order = [
        "What Changed",
        "Evolution Timeline",
        "System Architecture",
        "Data Flow",
        "Module Dependency Graph",
        "Code Health Summary",
    ]

    section_map = {title.strip(): body for title, body in report_sections}

    def find_section(name):
        if name in section_map:
            return section_map[name]
        for k, v in section_map.items():
            if name.lower() in k.lower():
                return v
        return None

    for name in section_order:
        body = find_section(name)
        if body:
            lines.append(f"### {name}")
            lines.append("")
            lines.append(body.strip())
            lines.append("")

    # Proposals filtered to files owned by this feature
    feature_proposals = [
        p for p in proposals
        if any(Path(f).name in feature_files for f in p.get("affected_files", []))
    ]

    if feature_proposals:
        lines.append("### Improvement Proposals")
        lines.append("")
        risk_icon = {"low": "🟢", "medium": "🟡", "high": "🔴"}
        for p in feature_proposals:
            icon = risk_icon.get(p["risk_level"], "⚪")
            lines.append(
                f"**[{p['id']}] {p['title']}** — {icon} {p['risk_level']} — {p['priority']}"
            )
            lines.append("")
            lines.append(f"*Problem:* {p['problem']}")
            lines.append("")
            lines.append(f"*Fix:* {p['proposed_fix']}")
            lines.append("")

    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def append_to_feature_doc(feature_path: Path, section_text: str):
    """Append a new versioned section to the local feature doc."""
    with open(feature_path, "a", encoding="utf-8") as f:
        f.write(section_text)


# ─── rclone Sync ──────────────────────────────────────────────────────────────

def rclone_available() -> bool:
    """Check rclone is installed."""
    return shutil.which("rclone") is not None


def rclone_remote_configured(remote: str) -> bool:
    """Check if the named rclone remote exists."""
    result = subprocess.run(
        ["rclone", "listremotes"], capture_output=True, text=True
    )
    return f"{remote}:" in result.stdout


def sync_features_to_drive() -> bool:
    """
    Sync Kyle/docs/features/ to Google Drive via rclone.
    Returns True on success.
    """
    if not rclone_available():
        print("  ⚠️  rclone not found. Install with: brew install rclone")
        return False

    if not rclone_remote_configured(RCLONE_REMOTE):
        print(f"  ⚠️  rclone remote '{RCLONE_REMOTE}' not configured.")
        print(f"       Run: python gdrive_setup.py")
        return False

    dest = f"{RCLONE_REMOTE}:{RCLONE_DRIVE_FOLDER}"
    result = subprocess.run(
        ["rclone", "sync", str(FEATURES_DIR), dest,
         "--drive-skip-gdocs",           # don't convert to Gdocs format
         "--progress",
         "--log-level", "ERROR"],        # only show errors
        capture_output=True, text=True,
    )

    if result.returncode != 0:
        print(f"  ⚠️  rclone sync failed:\n{result.stderr[:400]}")
        return False

    return True


# ─── Main Entry Point ─────────────────────────────────────────────────────────

def sync_to_drive(
    report_sections: list[tuple[str, str]],
    proposals: list[dict],
    commit_sha: str,
    repo_root: Path,
    override_features: list[str] | None = None,
) -> dict[str, str]:
    """
    Write feature sections locally and sync to Google Drive.
    Returns {feature: local_path_str} for updated features.
    Pass override_features to bypass git diff detection (e.g. bootstrap mode).
    """
    if override_features:
        features = override_features
    else:
        changed_files = get_changed_files(repo_root)
        features      = detect_features(changed_files)

    print(f"\n  📋 Features touched: {', '.join(features)}")

    updated: dict[str, str] = {}

    for feature in features:
        filename     = feature_to_filename(feature)
        feature_path = FEATURES_DIR / filename

        ensure_feature_doc(feature_path, feature)
        version      = get_feature_version(feature_path) + 1

        section_text = compose_feature_section(
            feature, version, commit_sha, report_sections, proposals
        )
        append_to_feature_doc(feature_path, section_text)
        updated[feature] = str(feature_path.relative_to(repo_root))
        print(f"  📝 {feature}: v{version} → {filename}")

    # Sync all feature docs to Drive
    print(f"\n  ☁️  Syncing to Drive ({RCLONE_REMOTE}:{RCLONE_DRIVE_FOLDER})…")
    if sync_features_to_drive():
        print("  ✅ Drive sync complete.")
    else:
        print("  📁 Feature docs saved locally (Drive sync skipped).")

    return updated
