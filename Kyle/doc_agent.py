#!/usr/bin/env python3
"""
ME135 Documentation Agent Swarm
================================
Generates evolution reports + improvement proposals on every git push.

Three agent personalities run in parallel:
  📜 HISTORIAN   — reads git diff, narrates what changed and why
  🏛  ARCHITECT   — reads all source, draws Mermaid system diagrams
  🔍 CRITIC      — hunts for bugs, design flaws, and improvement opportunities

Their work is assembled into a timestamped Markdown report in docs/.
Improvement proposals are presented interactively for user approval.

Usage:
    python doc_agent.py                         # Full interactive run
    python doc_agent.py --pre-push              # Called by git hook (non-interactive)
    python doc_agent.py --implement PROP_ID     # Apply a specific approved proposal
"""

import asyncio
import argparse
import json
import os
import sqlite3
import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path

import anthropic
from gdrive_sync import sync_to_drive, detect_features, get_changed_files

# ─── Paths ────────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).parent          # Kyle/
REPO_ROOT    = PROJECT_ROOT.parent            # ME-135-235-Proj/
DOCS_DIR     = PROJECT_ROOT / "docs"
DOCS_DIR.mkdir(exist_ok=True)

# ─── SQLite — Proposal History DB ────────────────────────────────────────────

DB_PATH = DOCS_DIR / "history.db"


def db_connect() -> sqlite3.Connection:
    """Connect to (or create) the proposal history database."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS proposals (
            id          TEXT,
            commit_sha  TEXT,
            report_date TEXT,
            title       TEXT,
            problem     TEXT,
            proposed_fix TEXT,
            affected_files TEXT,
            risk_level  TEXT,
            priority    TEXT,
            decision    TEXT DEFAULT 'pending',   -- pending | approved | denied
            implemented_at TEXT,
            PRIMARY KEY (id, commit_sha)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            commit_sha  TEXT PRIMARY KEY,
            report_file TEXT,
            generated_at TEXT
        )
    """)
    conn.commit()
    return conn


def db_save_proposals(conn: sqlite3.Connection, proposals: list[dict], commit_sha: str, report_date: str):
    for p in proposals:
        conn.execute("""
            INSERT OR IGNORE INTO proposals
              (id, commit_sha, report_date, title, problem, proposed_fix,
               affected_files, risk_level, priority)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            p["id"], commit_sha, report_date, p["title"], p["problem"],
            p["proposed_fix"], json.dumps(p["affected_files"]),
            p["risk_level"], p["priority"],
        ))
    conn.commit()


def db_record_decision(conn: sqlite3.Connection, proposal_id: str, commit_sha: str, decision: str):
    conn.execute(
        "UPDATE proposals SET decision = ? WHERE id = ? AND commit_sha = ?",
        (decision, proposal_id, commit_sha),
    )
    conn.commit()


def db_record_implementation(conn: sqlite3.Connection, proposal_id: str, commit_sha: str):
    conn.execute(
        "UPDATE proposals SET implemented_at = ? WHERE id = ? AND commit_sha = ?",
        (datetime.now().isoformat(), proposal_id, commit_sha),
    )
    conn.commit()


def db_save_report(conn: sqlite3.Connection, commit_sha: str, report_file: str):
    conn.execute(
        "INSERT OR REPLACE INTO reports VALUES (?, ?, ?)",
        (commit_sha, report_file, datetime.now().isoformat()),
    )
    conn.commit()


# ─── Models ───────────────────────────────────────────────────────────────────

MODEL_THINKER = "claude-opus-4-6"    # Historian + Critic — deep reasoning
MODEL_WRITER  = "claude-sonnet-4-6"  # Architect — fast, structured output

# ─── Shared state (populated by agent tool calls) ─────────────────────────────

_report_sections: list[tuple[str, str]] = []   # (title, markdown_body)
_proposals:       list[dict]            = []    # structured improvement proposals

# ─── Project file reader ──────────────────────────────────────────────────────

def collect_project_files() -> dict[str, str]:
    """Read all source files relevant to the project."""
    files: dict[str, str] = {}
    globs = [
        "*.py", "*.md", "*.txt", "*.yaml",
        "agent_outputs/*.py", "agent_outputs/*.md",
        "agent_outputs/*.cpp", "agent_outputs/*.yaml",
        "agent_outputs/*.ini",
    ]
    for pattern in globs:
        for f in sorted(PROJECT_ROOT.glob(pattern)):
            if "__pycache__" in str(f):
                continue
            try:
                key = f.relative_to(PROJECT_ROOT).as_posix()
                files[key] = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass
    return files


def get_git_context() -> str:
    """Recent git history, stat diff, and full diff for Kyle/ subdirectory."""
    parts: list[str] = []

    def run(cmd):
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT))
        return r.stdout.strip()

    log   = run(["git", "log", "--oneline", "-10"])
    stat  = run(["git", "diff", "HEAD~1", "--stat", "--", "Kyle/"])
    show  = run(["git", "show", "--stat", "HEAD"])

    parts.append(f"### Recent commits\n```\n{log}\n```")
    parts.append(f"### HEAD commit\n```\n{show}\n```")

    if stat:
        parts.append(f"### Diff stat (Kyle/)\n```\n{stat}\n```")

    # Full diff, truncated to 14 KB to stay within context
    diff_text = run(["git", "diff", "HEAD~1", "--", "Kyle/"])
    if diff_text:
        if len(diff_text) > 14_000:
            diff_text = diff_text[:14_000] + "\n\n[... diff truncated ...]"
        parts.append(f"### Full diff (Kyle/)\n```diff\n{diff_text}\n```")

    return "\n\n".join(parts)


# ─── Tools ────────────────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "read_source",
        "description": (
            "Read a project source file. Pass the relative path from Kyle/ "
            "(e.g. 'agent_outputs/cv_pipeline.py'). Returns file content up to 8 KB."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Path relative to Kyle/"}
            },
            "required": ["filename"],
            "additionalProperties": False,
        },
    },
    {
        "name": "write_section",
        "description": (
            "Add a named section to the evolution report. "
            "Use Mermaid fenced blocks (```mermaid) for diagrams. "
            "Markdown only — no raw HTML."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "section_title": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["section_title", "content"],
            "additionalProperties": False,
        },
    },
    {
        "name": "write_proposals",
        "description": (
            "Register improvement proposals found in the code. "
            "Each proposal must be specific and actionable. "
            "risk_level: low | medium | high. "
            "priority: must-fix | nice-to-have | future."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "proposals": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id":            {"type": "string"},
                            "title":         {"type": "string"},
                            "problem":       {"type": "string"},
                            "proposed_fix":  {"type": "string"},
                            "affected_files":{"type": "array", "items": {"type": "string"}},
                            "risk_level":    {"type": "string", "enum": ["low", "medium", "high"]},
                            "priority":      {"type": "string", "enum": ["must-fix", "nice-to-have", "future"]},
                        },
                        "required": ["id", "title", "problem", "proposed_fix",
                                     "affected_files", "risk_level", "priority"],
                    },
                }
            },
            "required": ["proposals"],
            "additionalProperties": False,
        },
    },
]


def execute_tool(name: str, inp: dict, project_files: dict) -> str:
    if name == "read_source":
        fn = inp["filename"]
        content = project_files.get(fn, "")
        if not content:
            return f"File not found or empty: {fn}"
        return content[:8000] + ("\n[...truncated...]" if len(content) > 8000 else "")

    elif name == "write_section":
        _report_sections.append((inp["section_title"], inp["content"]))
        return f"✓ Section '{inp['section_title']}' added."

    elif name == "write_proposals":
        count = len(inp["proposals"])
        _proposals.extend(inp["proposals"])
        return f"✓ {count} proposal(s) registered."

    return f"Unknown tool: {name}"


# ─── Agent runner ─────────────────────────────────────────────────────────────

async def run_agent(
    client: anthropic.AsyncAnthropic,
    agent_id: str,
    system_prompt: str,
    task_prompt: str,
    model: str = MODEL_WRITER,
    project_files: dict | None = None,
    max_turns: int = 14,
) -> str:
    project_files = project_files or {}
    print(f"  ▶ [{agent_id}] starting…")
    messages = [{"role": "user", "content": task_prompt}]
    final_text = ""

    for turn in range(max_turns):
        async with client.messages.stream(
            model=model,
            max_tokens=8192,
            thinking={"type": "adaptive"},
            system=system_prompt,
            tools=TOOLS,
            messages=messages,
        ) as stream:
            response = await stream.get_final_message()

        messages.append({"role": "assistant", "content": response.content})

        for block in response.content:
            if hasattr(block, "text"):
                final_text = block.text

        if response.stop_reason == "end_turn":
            break

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = execute_tool(block.name, block.input, project_files)
                    print(f"    ⚙ [{agent_id}] {block.name}({block.input.get('filename', block.input.get('section_title', ''))}) → {str(result)[:60]}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
            messages.append({"role": "user", "content": tool_results})
        else:
            break

    print(f"  ✓ [{agent_id}] complete ({turn + 1} turns)")
    return final_text


# ─── Agent definitions ────────────────────────────────────────────────────────

def make_historian_agent(git_context: str, file_list: str) -> dict:
    return {
        "agent_id": "historian",
        "model": MODEL_THINKER,
        "system_prompt": textwrap.dedent("""
            ## Personality: The Historian 📜
            You are a technical historian embedded in an engineering team.
            Your skill stack:
              - **Technical Writer**: Crystal-clear, jargon-free prose
              - **Git Workflow Master**: Expert git diff analysis and commit archaeology
              - **Project Shepherd**: Understands project trajectories and milestones

            You write brief, vivid accounts of what changed and WHY — not just what.
            Every section you write goes into a living project evolution document.
            Use write_section to record your findings. Be concise but never vague.
        """).strip(),
        "task_prompt": textwrap.dedent(f"""
            Analyze the git history below and write two report sections:

            1. **"What Changed"** — A bullet-list summary of every meaningful code change.
               - Group changes by subsystem (CV pipeline, serial protocol, ESP32, etc.)
               - For each change, state WHAT changed and WHY it matters (infer from code context).
               - If this is the first commit, summarize the initial architecture instead.

            2. **"Evolution Timeline"** — A Mermaid timeline or gitGraph diagram showing
               the commit history and which subsystems each commit touched.

            Git context:
            {git_context}

            Files in project: {file_list}

            Read any source files you need for context. Write both sections via write_section.
        """).strip(),
    }


def make_architect_agent(file_list: str) -> dict:
    return {
        "agent_id": "architect",
        "model": MODEL_WRITER,
        "system_prompt": textwrap.dedent("""
            ## Personality: The Architect 🏛
            You are a system architect who communicates entirely through diagrams and structure.
            Your skill stack:
              - **Software Architect**: Domain-driven design, data flow, component boundaries
              - **Technical Writer**: Diagrams over prose — always Mermaid-first
              - **Backend Architect**: Understands serialization, concurrency, and performance

            Rules:
            - Every diagram must use Mermaid syntax (```mermaid blocks).
            - Minimize prose. Let diagrams do the talking.
            - Include data sizes/rates on arrows when known (e.g., "15,005 B/frame").
            - Use write_section for each diagram/section you produce.
        """).strip(),
        "task_prompt": textwrap.dedent(f"""
            Read the project source files and produce THREE diagram sections:

            1. **"System Architecture"** — Full Mermaid flowchart of the complete pipeline:
               PS3 Eye → Jetson (CV) → Serial → ESP32 → Display.
               Include: frame sizes, baud rates, data types, GPU/CPU annotations.

            2. **"Data Flow"** — Mermaid sequence diagram showing one frame's journey
               from camera capture to LED panel illumination.
               Include timing estimates and byte counts at each step.

            3. **"Module Dependency Graph"** — Mermaid graph showing how Python modules
               import/depend on each other. Show the class interface boundaries.

            Available files: {file_list}

            Use read_source to read each file before diagramming it.
            Write all sections via write_section.
        """).strip(),
    }


def make_critic_agent(file_list: str) -> dict:
    return {
        "agent_id": "critic",
        "model": MODEL_THINKER,
        "system_prompt": textwrap.dedent("""
            ## Personality: The Critic 🔍
            You are a ruthless but constructive code reviewer and systems thinker.
            Your skill stack:
              - **Code Reviewer**: Correctness, security, maintainability
              - **Security Engineer**: Injection, resource leaks, race conditions
              - **Performance Benchmarker**: Throughput bottlenecks, memory waste
              - **SRE**: Reliability, error handling, failure modes

            You do NOT fix code yourself. You write precise, actionable improvement proposals
            using write_proposals. Each proposal must name the exact file and function,
            describe the problem with evidence from the code, and propose a specific fix.

            Also write a "Code Health Summary" section via write_section summarizing
            overall code quality in ≤ 150 words.
        """).strip(),
        "task_prompt": textwrap.dedent(f"""
            Carefully read ALL project source files and identify improvements.
            Focus on:

            1. **Correctness** — bugs, off-by-one errors, wrong assumptions
            2. **Reliability** — missing error handling, race conditions, resource leaks
            3. **Performance** — unnecessary copies, blocking I/O in hot loops, serial bottlenecks
            4. **Security** — shell injection, hardcoded credentials, unchecked inputs
            5. **Design** — tight coupling, missing abstractions, duplicated logic

            Available files: {file_list}

            Read every Python file, the ESP32 .cpp file, and the config.yaml.
            Use read_source for each file.

            Then:
            - Call write_proposals with ALL proposals found (min 2, max 8 for this codebase).
            - Call write_section for "Code Health Summary" (≤150 words, overall grade A–F).
        """).strip(),
    }


# ─── Report assembler ─────────────────────────────────────────────────────────

def assemble_report(git_context: str, commit_sha: str) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# ME135 Evolution Report",
        f"",
        f"**Date:** {timestamp} &nbsp;|&nbsp; **Commit:** `{commit_sha}`",
        f"",
        "---",
        "",
    ]

    # Ordered sections: Historian first, then Architect, then Critic's health summary
    section_order = [
        "What Changed",
        "Evolution Timeline",
        "System Architecture",
        "Data Flow",
        "Module Dependency Graph",
        "Code Health Summary",
    ]

    # Build a lookup (case-insensitive)
    section_map: dict[str, str] = {}
    for title, body in _report_sections:
        section_map[title.strip()] = body

    def find_section(name: str) -> tuple[str, str] | None:
        # Exact match first
        if name in section_map:
            return name, section_map[name]
        # Partial match
        for k, v in section_map.items():
            if name.lower() in k.lower():
                return k, v
        return None

    added = set()
    for name in section_order:
        result = find_section(name)
        if result:
            title, body = result
            lines.append(f"## {title}")
            lines.append("")
            lines.append(body.strip())
            lines.append("")
            lines.append("---")
            lines.append("")
            added.add(title)

    # Any remaining sections not in the order list
    for title, body in _report_sections:
        if title not in added:
            lines.append(f"## {title}")
            lines.append("")
            lines.append(body.strip())
            lines.append("")
            lines.append("---")
            lines.append("")

    # Improvement proposals appendix
    if _proposals:
        lines.append("## Improvement Proposals")
        lines.append("")
        lines.append("| # | Title | Priority | Risk | Files |")
        lines.append("|---|-------|----------|------|-------|")
        for p in _proposals:
            files = ", ".join(p.get("affected_files", []))
            lines.append(
                f"| {p['id']} | {p['title']} | {p['priority']} | {p['risk_level']} | `{files}` |"
            )
        lines.append("")

        for p in _proposals:
            lines.append(f"### {p['id']}: {p['title']}")
            lines.append(f"**Priority:** {p['priority']} &nbsp;|&nbsp; **Risk:** {p['risk_level']}")
            lines.append("")
            lines.append(f"**Problem:** {p['problem']}")
            lines.append("")
            lines.append(f"**Proposed fix:** {p['proposed_fix']}")
            lines.append("")
            lines.append(f"**Affected files:** {', '.join(p['affected_files'])}")
            lines.append("")
            lines.append("---")
            lines.append("")

    return "\n".join(lines)


# ─── Implementer agent ────────────────────────────────────────────────────────

async def run_implementer(client: anthropic.AsyncAnthropic, proposal: dict, project_files: dict):
    """Runs a targeted implementer agent for an approved proposal."""

    IMPL_TOOLS = [
        {
            "name": "apply_fix",
            "description": (
                "Apply a code fix. Provide the filename (relative to Kyle/), "
                "the exact old_text to replace, and new_text to replace it with. "
                "Must be a minimal, surgical change."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "filename":  {"type": "string"},
                    "old_text":  {"type": "string"},
                    "new_text":  {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["filename", "old_text", "new_text", "rationale"],
                "additionalProperties": False,
            },
        },
        {
            "name": "read_source",
            "description": "Read a project source file (relative to Kyle/).",
            "input_schema": {
                "type": "object",
                "properties": {"filename": {"type": "string"}},
                "required": ["filename"],
                "additionalProperties": False,
            },
        },
    ]

    def execute_impl_tool(name, inp):
        if name == "read_source":
            fn = inp["filename"]
            return project_files.get(fn, f"Not found: {fn}")[:8000]
        elif name == "apply_fix":
            fn = inp["filename"]
            path = (PROJECT_ROOT / fn).resolve()
            # Safety: must stay inside the repo
            try:
                path.relative_to(PROJECT_ROOT.resolve())
            except ValueError:
                return f"ERROR: Path traversal denied: '{fn}'"
            if not path.exists():
                return f"ERROR: File not found: {path}"
            old = inp["old_text"]
            new = inp["new_text"]
            content = path.read_text(encoding="utf-8")
            if old not in content:
                return f"ERROR: old_text not found in {fn}. No change made."
            updated = content.replace(old, new, 1)
            path.write_text(updated, encoding="utf-8")
            print(f"\n  ✅ Applied fix to {fn}:")
            print(f"     {inp['rationale']}")
            return f"✓ Applied to {fn}"
        return f"Unknown: {name}"

    system = textwrap.dedent(f"""
        ## Personality: The Implementer 🔧
        You are a precise, minimal-change software engineer.
        Your job: implement EXACTLY one approved proposal — nothing more.
        - Read the relevant file(s) first via read_source.
        - Make the smallest correct change that fixes the stated problem.
        - Use apply_fix for each atomic change needed.
        - Do NOT add features, refactor unrelated code, or change style.
    """).strip()

    task = textwrap.dedent(f"""
        Implement this approved proposal:

        **Title:** {proposal['title']}
        **Problem:** {proposal['problem']}
        **Proposed fix:** {proposal['proposed_fix']}
        **Files to modify:** {', '.join(proposal['affected_files'])}

        Read each affected file, then apply minimal surgical fixes.
    """).strip()

    print(f"\n  ▶ [implementer] applying: {proposal['title']}")
    messages = [{"role": "user", "content": task}]

    for turn in range(10):
        async with client.messages.stream(
            model=MODEL_THINKER,
            max_tokens=4096,
            thinking={"type": "adaptive"},
            system=system,
            tools=IMPL_TOOLS,
            messages=messages,
        ) as stream:
            response = await stream.get_final_message()

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            break

        if response.stop_reason == "tool_use":
            results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = execute_impl_tool(block.name, block.input)
                    results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
            messages.append({"role": "user", "content": results})
        else:
            break

    print(f"  ✓ [implementer] done")


# ─── Interactive proposal review ──────────────────────────────────────────────

def print_proposals():
    if not _proposals:
        print("\n  No improvement proposals.")
        return

    risk_colors = {"low": "🟢", "medium": "🟡", "high": "🔴"}
    print(f"\n{'═'*62}")
    print(f"  🔍 Critic found {len(_proposals)} improvement proposal(s):")
    print(f"{'═'*62}")
    for p in _proposals:
        icon = risk_colors.get(p["risk_level"], "⚪")
        print(f"\n  [{p['id']}] {p['title']}")
        print(f"       Priority: {p['priority']}  Risk: {icon} {p['risk_level']}")
        print(f"       Problem:  {p['problem'][:120]}")
        print(f"       Fix:      {p['proposed_fix'][:120]}")
        print(f"       Files:    {', '.join(p['affected_files'])}")
    print()


async def interactive_proposal_review(client, project_files, commit_sha: str):
    """Present proposals and run implementer for approved ones."""
    print_proposals()
    if not _proposals:
        return

    conn = db_connect()
    approved = []
    try:
        tty = open("/dev/tty", "r")
    except Exception:
        tty = sys.stdin

    for p in _proposals:
        sys.stdout.write(f"  Apply [{p['id']}] {p['title']}? [y/N] ")
        sys.stdout.flush()
        answer = tty.readline().strip().lower()
        if answer in ("y", "yes"):
            approved.append(p)
            db_record_decision(conn, p["id"], commit_sha, "approved")
        else:
            db_record_decision(conn, p["id"], commit_sha, "denied")

    if tty is not sys.stdin:
        tty.close()

    if not approved:
        print("\n  No proposals approved — skipping implementation.")
        return

    print(f"\n  Implementing {len(approved)} proposal(s)…")
    for p in approved:
        await run_implementer(client, p, project_files)
        db_record_implementation(conn, p["id"], commit_sha)


# ─── Main ─────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="ME135 Documentation Agent Swarm")
    parser.add_argument("--pre-push",  action="store_true",
                        help="Non-interactive mode (called by git hook)")
    parser.add_argument("--bootstrap", action="store_true",
                        help="v1 pass: document ALL features from scratch, ignoring git diff")
    parser.add_argument("--implement", metavar="PROPOSAL_ID",
                        help="Apply a specific proposal from docs/PENDING_IMPROVEMENTS.md")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set.", file=sys.stderr)
        sys.exit(1)

    client = anthropic.AsyncAnthropic(api_key=api_key)

    # ── Implement a specific proposal ────────────────────────────────────────
    if args.implement:
        pending_path = DOCS_DIR / "PENDING_IMPROVEMENTS.json"
        if not pending_path.exists():
            print(f"ERROR: No pending improvements file found at {pending_path}")
            sys.exit(1)
        all_props = json.loads(pending_path.read_text())
        prop = next((p for p in all_props if p["id"] == args.implement), None)
        if not prop:
            print(f"ERROR: Proposal '{args.implement}' not found.")
            sys.exit(1)
        project_files = collect_project_files()
        await run_implementer(client, prop, project_files)
        return

    # ── Normal documentation run ──────────────────────────────────────────────
    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print("║          ME135 Documentation Agent Swarm                 ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print()

    project_files = collect_project_files()
    file_list     = ", ".join(project_files.keys())
    git_context   = get_git_context()

    # Get current commit SHA
    try:
        commit_sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(REPO_ROOT), capture_output=True, text=True
        ).stdout.strip()
    except Exception:
        commit_sha = "unknown"

    if args.bootstrap:
        # Bootstrap: treat every known file as changed so all features get v1 docs
        from gdrive_sync import FEATURE_MAP
        changed_files = list(FEATURE_MAP.keys())
        features      = list(set(FEATURE_MAP.values()))
        git_context   = (
            "### Bootstrap v1 pass\n"
            "This is the FIRST documentation run — there is no prior version.\n"
            "Document the CURRENT state of every feature as v1.\n\n"
            + git_context
        )
    else:
        changed_files = get_changed_files(REPO_ROOT)
        features      = detect_features(changed_files)

    print(f"  Commit:   {commit_sha}")
    print(f"  Files:    {len(project_files)} source files loaded")
    print(f"  Features: {', '.join(sorted(features))}")
    print(f"  Mode:     {'Bootstrap v1' if args.bootstrap else 'Incremental'}")
    print(f"  Output:   docs/")
    print()

    # ── Define agents ────────────────────────────────────────────────────────
    historian_cfg = make_historian_agent(git_context, file_list)
    architect_cfg = make_architect_agent(file_list)
    critic_cfg    = make_critic_agent(file_list)

    # ── Run all three agents in parallel ────────────────────────────────────
    print("  Launching agents (parallel)…\n")
    await asyncio.gather(
        run_agent(client, historian_cfg["agent_id"], historian_cfg["system_prompt"],
                  historian_cfg["task_prompt"], historian_cfg["model"], project_files),
        run_agent(client, architect_cfg["agent_id"], architect_cfg["system_prompt"],
                  architect_cfg["task_prompt"], architect_cfg["model"], project_files),
        run_agent(client, critic_cfg["agent_id"],    critic_cfg["system_prompt"],
                  critic_cfg["task_prompt"],    critic_cfg["model"],    project_files),
    )

    # ── Assemble report ──────────────────────────────────────────────────────
    timestamp  = datetime.now().strftime("%Y-%m-%d_%H%M")
    report_md  = assemble_report(git_context, commit_sha)
    report_path = DOCS_DIR / f"report_{timestamp}.md"
    report_path.write_text(report_md, encoding="utf-8")
    print(f"\n  📄 Report written → {report_path.relative_to(REPO_ROOT)}")

    # ── Persist to SQLite ─────────────────────────────────────────────────────
    conn = db_connect()
    db_save_report(conn, commit_sha, str(report_path.relative_to(REPO_ROOT)))
    if _proposals:
        db_save_proposals(conn, _proposals, commit_sha, timestamp)

    # Save proposals as JSON for --implement flag
    if _proposals:
        pending_path = DOCS_DIR / "PENDING_IMPROVEMENTS.json"
        pending_path.write_text(json.dumps(_proposals, indent=2), encoding="utf-8")
        print(f"  💾 Proposals saved → {pending_path.relative_to(REPO_ROOT)}")

    # Update docs/README.md index
    index_path = DOCS_DIR / "README.md"
    all_reports = sorted(DOCS_DIR.glob("report_*.md"), reverse=True)
    index_lines = ["# ME135 Evolution Reports\n"]
    for r in all_reports[:20]:
        index_lines.append(f"- [{r.stem}](./{r.name})")
    index_path.write_text("\n".join(index_lines) + "\n", encoding="utf-8")

    # ── Sync to Google Drive feature docs ────────────────────────────────────
    drive_urls = sync_to_drive(
        _report_sections, _proposals, commit_sha, REPO_ROOT,
        override_features=features if args.bootstrap else None,
    )
    if drive_urls:
        print()
        print("  🔗 Google Drive feature docs updated:")
        for feature, url in drive_urls.items():
            print(f"     {feature}: {url}")

        # Record Drive URLs in SQLite
        conn_drive = db_connect()
        for feature, url in drive_urls.items():
            conn_drive.execute(
                "INSERT OR IGNORE INTO reports VALUES (?, ?, ?)",
                (f"{commit_sha}:{feature}", url, datetime.now().isoformat()),
            )
        conn_drive.commit()

    # ── Interactive improvement review (skip in --pre-push hook) ─────────────
    if not args.pre_push:
        await interactive_proposal_review(client, project_files, commit_sha)
    else:
        # Non-interactive: just print proposals summary
        print_proposals()

    print()
    print("  ✅ Documentation complete.")
    print()


if __name__ == "__main__":
    asyncio.run(main())
