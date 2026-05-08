# ME135 | Documentation System

> Evolution log — one section per commit.

---

## v1 — 2026-03-13 23:46 — `179b802`

### What Changed

**This is the v1 bootstrap run — documenting the full initial architecture across 7 commits.**

---

### 🎥 Computer Vision Pipeline (`cv_pipeline.py`, `gpu_accelerated.py`)

- **CPU pipeline** captures PS3 Eye frames (640×480 @ 60fps), applies MOG2/KNN/static-median background subtraction, morphological cleanup, small-blob rejection, then outputs a **400×300 binary matrix** (0 = background, 1 = human). Three calibration methods give flexibility depending on scene lighting.
- **GPU pipeline** mirrors the exact same API but runs all processing on CUDA via `cv2.cuda_GpuMat`. Uses JetPack 6/OpenCV 4.8 return-value style calls. Falls back to CPU transparently if CUDA is unavailable. Performance: ~2ms/frame (GPU) vs ~8ms/frame (CPU) on Jetson Orin Nano Super.
- **Why it matters:** The dual-pipeline design means the system runs on any hardware — a laptop for development, a Jetson for deployment — with a single config toggle (`use_gpu: true`).

### 📡 Serial Communication Protocol (`serial_protocol.py`, `PROTOCOL_SPEC.md`)

- **Wire format:** `[0xAA 0x55][LEN_H LEN_L][PAYLOAD…][CRC_H CRC_L][0x55 0xAA]` — 15,008 bytes/frame at 2 Mbaud UART. CRC-16/CCITT-FALSE error detection with ACK(0x06)/NAK(0x15) flow control.
- **Downsampling:** The Python sender downsamples from 400×300 (CV resolution) to 108×108 (physical LED panel), then bit-packs MSB-first into 1,458 bytes before framing. This keeps CV accuracy high while matching hardware.
- **Retry logic:** Up to 3 retransmits on NAK/timeout; 10 consecutive failures trigger a safety shutdown.
- **Why it matters:** This is the critical real-time bridge between the Jetson brain and ESP32 muscles. The spec is pinned in a standalone Markdown doc so hardware and firmware teams stay in sync.

### 🔌 ESP32 Firmware (`esp32_main.cpp`, `platformio.ini`)

- Receives framed packets on HardwareSerial1 (GPIO 16/17), validates CRC, sends ACK/NAK, and pushes bit-unpacked data to an 11,664-LED WS2812B NeoPixel strip (108×108 panel).
- **Watchdog:** If no valid frame arrives within 5 seconds, the display blanks — a course-mandated safety feature.
- **Why it matters:** This is the embedded endpoint. CRC + watchdog together mean the display never shows corrupted or stale data.

### 🧩 System Integration (`main.py`, `config.yaml`)

- `main.py` orchestrates the full loop: config load → GPU/CPU pipeline selection → calibration (with 10-second countdown for the human to leave the frame) → live capture → serial transmit → OpenCV preview window. Graceful SIGINT/SIGTERM shutdown.
- `config.yaml` is the **single source of truth** for every tuneable parameter: camera device, calibration method, processing thresholds, serial port, LED panel size, LabVIEW hub (disabled), safety watchdogs.
- **Why it matters:** One file controls every parameter across Python and C++. No magic numbers buried in source.

### 🤖 Agent Swarm — Code Generation (`orchestrator.py`)

- Spawns 7 parallel Claude agents (using Opus for architects, Sonnet for coders) in an async tool-use loop. Each agent reads existing files and writes new ones into `agent_outputs/`. Agents include: Hardware Scout, CV Engineer, GPU Accelerator, Serial/Protocol Specialist, ESP32 Firmware Engineer, Integration Lead, and Docs Lead.
- **Why it matters:** This is how the project's initial codebase was generated — a one-shot swarm that produced every file in `agent_outputs/`.

### 📝 Documentation System (`doc_agent.py`, `gdrive_sync.py`, `gdrive_setup.py`)

- **Doc Agent** runs 3 personalities in parallel on every git push: Historian (narrates changes), Architect (draws Mermaid diagrams), Critic (finds bugs and proposals). Uses tool calls (`read_source`, `write_section`, `write_proposals`) and assembles results into timestamped Markdown reports.
- **Feature mapping** (`gdrive_sync.py`): A `FEATURE_MAP` dict routes each source file to a named feature (e.g., `cv_pipeline.py` → "Computer Vision Pipeline"). Versioned sections are appended to per-feature Markdown files in `docs/features/`.
- **`--bootstrap` mode** (added in HEAD): Forces all features to be documented from scratch by treating every mapped file as "changed." Prepends a v1 context note to the git context so agents know there's no prior version.
- **Google Drive sync** via rclone: After each doc run, `rclone sync` pushes feature docs to a shared "ME135 Feature Reports" Drive folder.
- **`gdrive_setup.py` rewrite** (HEAD commit): Replaced a 13-step interactive wizard with a fully non-interactive `rclone config create` + `rclone config reconnect` flow. Auto-deletes any existing remote to clear stale/bad `client_id` values. Reduced from 79 lines of instructions to a clean programmatic sequence.
- **Why it matters:** The documentation system is self-sustaining — every push auto-generates evolution docs and syncs them to Drive for the course deliverables. The setup rewrite means teammates can configure Drive sync without following a 13-step manual.

### 📦 Dependencies & Hardware (`requirements.txt`, `hardware_recommendation.md`, `SETUP.md`)

- Python deps: `anthropic`, `opencv-python-headless`, `numpy`, `pyserial`, `pyyaml`, `tqdm`. Optional CUDA via JetPack system OpenCV.
- Recommended hardware: Jetson Orin Nano Super ($249, 1024 CUDA cores, 8GB LPDDR5) as primary; ESP32-DevKitC as display controller.

### Evolution Timeline

```mermaid
gitGraph
    commit id: "60d0f1a" tag: "Lab 2" type: NORMAL
    commit id: "a15d0fb" type: NORMAL
    commit id: "0573d44" tag: "Project Genesis" type: HIGHLIGHT
    commit id: "ffb387f" type: NORMAL
    commit id: "5c494f9" type: NORMAL
    commit id: "b5f572c" type: NORMAL
    commit id: "179b802" tag: "v1-bootstrap" type: HIGHLIGHT
```

### Commit-by-Commit Subsystem Map

| Commit | Message | Subsystems Touched |
|--------|---------|-------------------|
| `60d0f1a` | added casestructure.vi for lab2 | LabVIEW (coursework, pre-project) |
| `a15d0fb` | added arrayaverages.vi, not finished | LabVIEW (coursework, pre-project) |
| `0573d44` | Add Kyle's camera processing project files | **CV Pipeline**, **Serial Protocol**, **ESP32 Firmware**, **System Integration**, **Agent Swarm**, **Config** — the "big bang" commit with all generated code |
| `ffb387f` | feat(docs): add documentation agent swarm | **Documentation System** — `doc_agent.py`, `gdrive_sync.py`, tool-use agents |
| `5c494f9` | feat(docs): add Google Drive feature report sync | **Documentation System** — Drive sync via Google API OAuth (first attempt) |
| `b5f572c` | fix(docs): replace Google API OAuth with rclone | **Documentation System** — abandoned Google Cloud Console OAuth in favor of rclone's bundled OAuth app |
| `179b802` | fix(setup): non-interactive rclone config, auto-clear bad client_id | **Documentation System** — `gdrive_setup.py` rewrite (non-interactive), `doc_agent.py` `--bootstrap` flag |

### Project Arc

```mermaid
timeline
    title ME135 Project Evolution
    section Coursework
        Lab exercises : casestructure.vi, arrayaverages.vi
    section Project Genesis
        All subsystems landed in one commit : CV pipeline (CPU + GPU)
                                            : Serial protocol + spec
                                            : ESP32 firmware
                                            : System integrator (main.py)
                                            : Agent swarm orchestrator
                                            : Config + hardware docs
    section Documentation Layer
        Doc agent swarm added : 3-personality AI doc system
        Drive sync v1 (Google OAuth) : First attempt at cloud sync
        Drive sync v2 (rclone) : Simpler auth, no admin console needed
        Setup hardened : Non-interactive config, auto-clear bad state
```

**Key observation:** The project has a distinctive "big bang + refinement" shape. Commit `0573d44` delivered the entire technical stack in one shot (via the agent swarm orchestrator). The four subsequent commits are all about building and hardening the *documentation infrastructure* around that core — a self-documenting system that evolves the docs on every push.

### Code Health Summary

**Overall Grade: B−**

The codebase shows strong architectural intent — clean separation between CPU/GPU pipelines, a well-defined serial protocol with CRC and ACK/NAK, proper signal handling, and a centralized config.yaml. The code is readable and well-commented throughout.

However, several **reliability-critical bugs** undermine the solid design:

1. **ESP32 RX buffer silently defaults to 256 bytes** due to a call-order bug, virtually guaranteeing frame loss at 2 Mbaud — this is a showstopper.
2. **Path traversal** in tool handlers gives LLM agents unsandboxed filesystem access.
3. **Protocol documentation is stale** — specs claim 15,000-byte payloads while code sends 1,458 bytes.
4. **`cv2.imshow` on headless Jetson** crashes the main loop.

Secondary issues include unused timeout parameters, missing serial flush, and camera resource leaks on constructor failure. The Python code quality is consistently good; the gaps are at integration boundaries — firmware init ordering, doc-code sync, and headless deployment.

### Improvement Proposals

**[PATH-TRAVERSAL-TOOLS] Path traversal in write_file / read_file tool handlers** — 🔴 high — must-fix

*Problem:* In orchestrator.py `execute_tool()`, the filename from LLM tool input is joined directly to OUTPUT_DIR without sanitization: `path = OUTPUT_DIR / tool_input['filename']`. A malicious or hallucinated filename like `../../etc/cron.d/evil` or `../orchestrator.py` would read/write outside the sandbox. The same pattern exists in doc_agent.py's tool dispatch for `read_source`.

*Fix:* Resolve the path and verify it stays inside the target directory: `path = (OUTPUT_DIR / tool_input['filename']).resolve(); if not str(path).startswith(str(OUTPUT_DIR.resolve())): return 'Error: path escapes sandbox'`. Apply the same guard in doc_agent.py's read_source handler.

**[GDRIVE-WHICH-NONPORTABLE] gdrive_setup.py and gdrive_sync.py use shell `which` command — fails on Windows** — 🟢 low — future

*Problem:* Both gdrive_setup.py and gdrive_sync.py detect rclone via `subprocess.run(['which', 'rclone'], ...)`. The `which` command does not exist on Windows. While the primary target is Jetson (Linux), the documentation agents and Drive sync may run on developer laptops.

*Fix:* Replace `subprocess.run(['which', 'rclone'], ...)` with `shutil.which('rclone') is not None` (stdlib, cross-platform).

**[PROP-002] gdrive_setup.py always deletes and recreates the remote** — 🟢 low — nice-to-have

*Problem:* The setup script unconditionally deletes any existing 'me135drive' remote and recreates it, forcing a full re-auth every time it runs. The comment says 'may have bad client_id' but this is a sledgehammer approach — a working config gets destroyed on re-run.

*Fix:* Add a verification step first: try `rclone lsd me135drive:` and if it succeeds, skip deletion. Only delete-and-recreate if the connection test fails. This makes the script idempotent without forcing re-auth.

---
## v2 — 2026-03-13 23:49 — `179b802`

### What Changed

This is the **v1 bootstrap pass** — first documentation of the entire codebase. Below is the initial architecture, followed by the most recent changes visible in the HEAD diff.

---

### Initial Architecture (v1 Snapshot)

**The system is a real-time human detection pipeline for UC Berkeley's ME135 course.** A PS3 Eye camera feeds frames into a CV pipeline running on an NVIDIA Jetson, which produces a 400×300 binary matrix (0 = background, 1 = human). That matrix is bit-packed and sent over 2 Mbaud UART to an ESP32, which drives an 108×108 WS2812B LED panel.

#### CV Pipeline (`cv_pipeline.py`, `gpu_accelerated.py`)
- **CPU path:** OpenCV MOG2/KNN/static-median background subtraction → threshold → morphological cleanup → contour filtering → 400×300 binary output.
- **GPU path:** Drop-in CUDA-accelerated replacement using `cv2.cuda` GpuMat operations. Falls back to CPU automatically when CUDA is unavailable. Claims ~2 ms/frame vs ~8 ms/frame on Jetson Orin Nano Super.
- Both paths share identical public APIs (`calibrate()`, `process_frame()`, `release()`), selected at runtime via `config.yaml`.

#### Serial Protocol (`serial_protocol.py`, `PROTOCOL_SPEC.md`)
- Downsamples 400×300 CV matrix to 108×108 (matching the LED panel), then bit-packs into 1,458 bytes.
- Framing: `[0xAA 0x55][LEN][PAYLOAD][CRC-16][0x55 0xAA]` with ACK/NAK flow control.
- CRC-16/CCITT-FALSE computed over payload only. Up to 3 retransmits on NAK or timeout.

#### ESP32 Firmware (`esp32_main.cpp`, `platformio.ini`)
- Blocking frame receiver with sync-byte state machine and 5-second watchdog.
- Verifies CRC, sends ACK/NAK, then maps bit-packed payload to 11,664 NeoPixels.
- Safety: blanks the display if no frame received within the watchdog window.

#### System Integration (`main.py`, `config.yaml`)
- Single YAML config is the source of truth for all tunable parameters (camera, calibration, processing, serial, display, safety).
- `main.py` orchestrates: config load → pipeline init → interactive calibration (with 10-second countdown) → live loop with FPS limiting, watchdog, and graceful shutdown on SIGINT/SIGTERM.
- Safety: shuts down after 10 consecutive serial errors; logs watchdog warnings.

#### Agent Swarm — Code Generator (`orchestrator.py`)
- Spawns 7 specialized Claude agents (hardware scout, CV engineer, serial architect, ESP32 firmware dev, integration lead, doc writer, LabVIEW advisor) in parallel async loops.
- Each agent gets tools (`write_file`, `read_file`) and runs up to 12 agentic turns.
- This is how the entire `agent_outputs/` codebase was generated.

#### Documentation System (`doc_agent.py`, `gdrive_sync.py`, `gdrive_setup.py`)
- **3-agent doc swarm:** Historian (narrates changes), Architect (draws diagrams), Critic (finds bugs). Runs on every `git push` via pre-push hook.
- **Feature-based doc structure:** `FEATURE_MAP` maps source files → feature names. Each feature gets its own evolving Markdown file with versioned sections.
- **Google Drive sync:** `rclone` syncs `docs/features/` to a shared Drive folder. No Google Cloud Console access needed — uses rclone's bundled OAuth app.
- **SQLite history DB:** Tracks proposals and reports across commits in `docs/history.db`.

---

### Recent Changes (HEAD diff: `179b802`)

#### Documentation System — Bootstrap Mode
- **`doc_agent.py`:** Added `--bootstrap` CLI flag. When set, treats *every* file in `FEATURE_MAP` as changed so all features get v1 documentation in a single run. Without this, the agent only documents files touched in the latest commit. Also: feature names are now sorted in console output for readability; mode label ("Bootstrap v1" vs "Incremental") printed for operator awareness.
- **`gdrive_sync.py`:** `sync_to_drive()` gained an `override_features` parameter. Bootstrap mode passes the full feature list directly, bypassing `git diff` detection. This ensures the Drive folder gets a complete set of feature docs on first run.

#### Documentation System — rclone Setup Hardening
- **`gdrive_setup.py` (major refactor, net −18 lines):** Eliminated the old 13-step interactive `rclone config` wizard. Now uses `rclone config create` non-interactively (with blank `client_id`/`client_secret` to use rclone's bundled OAuth app), then triggers `rclone config reconnect` for the browser-based sign-in. **Key fix:** always deletes any existing remote before recreating — this auto-clears stale or bad `client_id` configurations that caused auth failures. Better error messages and troubleshooting hints on failure.

### Evolution Timeline

```mermaid
gitGraph
    commit id: "60d0f1a" tag: "Lab 2" type: HIGHLIGHT
    commit id: "a15d0fb" tag: "Lab Arrays"
    commit id: "0573d44" tag: "Project Drop"
    commit id: "ffb387f" tag: "Doc Agent"
    commit id: "5c494f9" tag: "Drive Sync"
    commit id: "b5f572c" tag: "rclone Switch"
    commit id: "179b802" tag: "v1-HEAD"
```

### Commit-to-Subsystem Map

| Commit | Message | Subsystems Touched |
|--------|---------|-------------------|
| `60d0f1a` | added casestructure.vi for lab2 | LabVIEW coursework (pre-project) |
| `a15d0fb` | added arrayaverages.vi (unfinished) | LabVIEW coursework (pre-project) |
| `0573d44` | Add Kyle's camera processing project files | **CV Pipeline**, **Serial Protocol**, **ESP32 Firmware**, **System Integration**, **Agent Swarm**, **Hardware & Platform** — initial code drop of all agent-generated outputs |
| `ffb387f` | feat(docs): add documentation agent swarm | **Documentation System** — `doc_agent.py` with 3-agent Historian/Architect/Critic swarm, SQLite proposal tracking |
| `5c494f9` | feat(docs): add Google Drive feature report sync | **Documentation System** — `gdrive_sync.py` (feature-based doc writer + rclone sync), `gdrive_setup.py` (initial interactive setup) |
| `b5f572c` | fix(docs): replace Google API OAuth with rclone | **Documentation System** — dropped Google Cloud Console OAuth dependency, switched to rclone's bundled OAuth app |
| `179b802` | fix(setup): non-interactive rclone config, auto-clear bad client_id | **Documentation System** — `gdrive_setup.py` refactored to non-interactive flow; `doc_agent.py` gained `--bootstrap` mode; `gdrive_sync.py` gained `override_features` |

### Project Trajectory

The repo tells a clear two-phase story:

1. **Phase 1 — Coursework** (`60d0f1a` → `a15d0fb`): Early LabVIEW exercises for ME135 labs. No project code yet.
2. **Phase 2 — Project Build** (`0573d44` → `179b802`): A single large code drop delivered the full detection pipeline (generated by the 7-agent orchestrator), immediately followed by three rapid iterations building and hardening the documentation-and-sync infrastructure. The team is investing heavily in automated documentation before the project enters its integration and testing phase.

### Code Health Summary

**Overall Grade: B−**

The codebase is well-structured with clear module boundaries (CV pipeline → serial protocol → ESP32 firmware), consistent logging, and a solid protocol design with CRC-16 and ACK/NAK flow control. Documentation is thorough. The CPU/GPU pipeline polymorphism is cleanly implemented.

**Critical issues** drag the grade down: (1) The ESP32 firmware calls `setRxBufferSize()` after `begin()`, meaning it's silently ignored — the 256-byte default buffer will overflow at 2 Mbaud, making serial communication non-functional. (2) Both `orchestrator.py` and `doc_agent.py` have path traversal vulnerabilities in their tool handlers — LLM agents can read/write arbitrary files. (3) `main.py` unconditionally opens a GUI window, crashing on headless Jetson deployments.

**Moderate concerns**: no config validation, no context-manager cleanup for camera handles, no frame-dropping on the ESP32 display bottleneck. Reliability under real-world faults needs hardening before demo day.

### Improvement Proposals

**[DOCAGENT-PATH-TRAVERSAL] Path traversal in doc_agent.py read_source tool handler** — 🟡 medium — must-fix

*Problem:* The `read_source` tool in `doc_agent.py` reads `PROJECT_ROOT / filename` where `filename` comes from an LLM agent's tool call. A malicious or hallucinated path like `../../.git/config` or `../../../etc/passwd` would be served without validation. The 8 KB truncation limits damage but doesn't prevent information disclosure.

*Fix:* In the `read_source` tool handler, resolve the full path with `.resolve()` and assert `resolved.is_relative_to(PROJECT_ROOT.resolve())`. Return an error message if the path escapes the project root.

**[GDRIVE-WHICH-PORTABILITY] rclone detection uses `which` — fails on Windows** — 🟢 low — nice-to-have

*Problem:* Both `gdrive_setup.py` and `gdrive_sync.py` call `subprocess.run(['which', 'rclone'], ...)` to check rclone availability. The `which` command does not exist on Windows (the equivalent is `where`). While the Jetson target is Linux, the orchestrator and doc agents can run on any developer machine, including Windows or WSL.

*Fix:* Replace `subprocess.run(['which', 'rclone'], ...)` with `shutil.which('rclone') is not None` (from Python stdlib `shutil`). `shutil.which()` is cross-platform and returns the path or None.

**[PROP-002] gdrive_setup.py always deletes and recreates remote — even when config is valid** — 🟢 low — nice-to-have

*Problem:* The setup script unconditionally deletes an existing 'me135drive' remote and recreates it, forcing a new OAuth browser flow every time. If the user runs setup twice or has a working config, they are forced to re-authenticate unnecessarily.

*Fix:* Add a validation step: run `rclone lsd me135drive:` before deleting. If it succeeds, print '✅ Already configured and working' and skip recreation. Only delete-and-recreate when the connection test fails.

---
## v3 — 2026-03-14 00:20 — `84333b8`

### What Changed

## Commit `84333b8` — 7-Agent Swarm Addresses All 14 Critic Proposals

This is the project's largest single commit: **537 insertions, 47 deletions across 11 files.** A swarm of 7 specialized AI agents (Security Engineer, Embedded Firmware Engineer, Backend Architect, AI Engineer, Workflow Optimizer, API Tester, Technical Writer) each tackled critic-identified flaws from the previous doc-agent run. Here's what landed, grouped by subsystem:

---

### 🔒 Security (Orchestrator + Doc Agent)

- **`orchestrator.py` — Path traversal guard on `write_file` / `read_file`:** Both tool handlers now reject any filename where `Path(filename).name != filename`. Previously, an agent could write to `../../etc/passwd` via the tool interface. This closes a sandbox escape vector in the agent swarm.
- **`doc_agent.py` — Path containment in `apply_fix`:** The implementer agent's file-editing tool now calls `.resolve()` and checks `.relative_to(PROJECT_ROOT)`, denying any path that escapes the repo. Same class of bug, different entry point.

### 🎛 ESP32 Firmware (`esp32_main.cpp`)

- **`setRxBufferSize()` moved before `begin()`:** The old order (`begin()` then `setRxBufferSize()`) made the buffer resize a silent no-op on ESP-IDF. The 4 KB RX buffer is critical — at 2 Mbaud, a full 1,466-byte frame arrives in ~6 ms, and the default 256-byte buffer would overflow instantly. This was a **latent data-loss bug** since the initial code was generated.
- **Stale-frame skip (`skipDisplay`):** When `strip.show()` blocks for ~350 ms (physics of clocking 11,664 WS2812B LEDs at 30 µs/LED), incoming UART frames pile up. The new logic checks `JetsonSerial.available() >= FRAME_HEADER_SIZE` after each display update; if data is already queued, it skips the *next* `updateDisplay()` to drain the buffer first. This prevents buffer overruns under the newly-raised 10 fps CV target.

### 📷 CV Pipeline (`main.py`, `cv_pipeline.py`, `gpu_accelerated.py`)

- **`main.py` — Preview gated on `args.show_preview`:** The old code always constructed a display image and called `cv2.imshow`, which crashes on a headless Jetson (no X server). Now the entire preview block is wrapped in `if args.show_preview`, making headless deployment safe.
- **`main.py` — `validate_config()` at startup:** Checks for all 6 required YAML sections (`camera`, `calibration`, `processing`, `serial`, `display`, `safety`) before any pipeline init. Catches typos and missing sections immediately instead of producing cryptic `KeyError` deep in pipeline constructors.
- **`cv_pipeline.py` + `gpu_accelerated.py` — Context managers (`__enter__`/`__exit__`):** Both pipeline classes now support `with` blocks, guaranteeing `release()` (which closes the camera) runs even on unhandled exceptions. Previously, a crash during processing could leave `/dev/video0` locked.
- **`gpu_accelerated.py` — KNN fallback warning:** When `config.yaml` says `method: knn` but the GPU pipeline silently uses CUDA MOG2 (CUDA has no KNN), users now get an explicit `logger.warning()` explaining what happened and how to suppress it. Eliminates a confusing "why doesn't my KNN config do anything?" debugging session.

### ⚙️ Config (`config.yaml`)

- **`fps_target` raised from 3 → 10:** The old value of 3 was the *display* physical limit (350 ms per `strip.show()`), but it was also throttling the CV capture loop. The new value of 10 lets the Jetson process frames faster; the ESP32's new `skipDisplay` logic handles the mismatch gracefully. Comment clarified to distinguish CV rate from display rate.

### 🔧 Tooling / DevOps (`gdrive_sync.py`, `gdrive_setup.py`)

- **`subprocess.run(["which", "rclone"])` → `shutil.which("rclone")`:** `which` is a Unix-only command and doesn't exist on Windows or inside some CI containers. `shutil.which()` is the portable Python equivalent — pure stdlib, no subprocess overhead.
- **`gdrive_setup.py` — Idempotent remote setup:** Old behavior: every run deleted and recreated the rclone remote, forcing a full OAuth re-auth. New behavior: tests if the existing remote works (`rclone lsd`); if yes, skips OAuth entirely. Only recreates if auth is actually broken. Remote creation logic extracted to `_create_remote()` helper.

### 🧪 Testing (`tests/test_serial_protocol.py`)

- **46 new pytest tests (427 lines):** Covers CRC-16 computation, bit-packing/unpacking round-trips, 400×300→108×108 downsampling, MSB bit ordering, and CRC integration with the framing protocol. This is the project's **first test suite** — a major milestone for a hardware-coupled system where serial bugs are expensive to debug on real hardware.

### 📝 Documentation (`MEMORY.md`, `orchestrator.py`)

- **Frame size corrected:** `15,005 bytes/frame` → `1,466 bytes/frame`. The old number was from an early design where the full 400×300 matrix was transmitted without downsampling or bit-packing. The actual wire format (108×108 bit-packed + 8B framing) is 10× smaller. The `PROJECT_CONTEXT` in `orchestrator.py` was updated to match, ensuring future agent runs generate code against the correct spec.

### Evolution Timeline

The project evolved from LabVIEW homework exercises into a multi-agent AI-driven embedded CV system across 8 commits. The timeline below shows the trajectory and which subsystems each commit touched.

```mermaid
gitGraph
    commit id: "60d0f1a" tag: "Lab2"
    commit id: "a15d0fb" tag: "Lab2b"
    commit id: "0573d44" tag: "v0.1-init"
    commit id: "ffb387f" tag: "doc-swarm"
    commit id: "5c494f9" tag: "gdrive-v1"
    commit id: "b5f572c" tag: "rclone-fix"
    commit id: "179b802" tag: "setup-fix"
    commit id: "076089c" tag: "bootstrap"
    commit id: "84333b8" tag: "swarm-fix-14"
```

### Subsystem touch map by commit

| Commit | Summary | CV Pipeline | ESP32 FW | Serial Proto | Agent Swarm | DevOps/Sync | Tests | Docs |
|--------|---------|:-----------:|:--------:|:------------:|:-----------:|:-----------:|:-----:|:----:|
| `60d0f1a` | LabVIEW lab2 case structure | | | | | | | |
| `a15d0fb` | LabVIEW array averages | | | | | | | |
| `0573d44` | **Initial architecture** — all project files | ✅ | ✅ | ✅ | ✅ | | | ✅ |
| `ffb387f` | Documentation agent swarm | | | | ✅ | | | ✅ |
| `5c494f9` | Google Drive sync for reports | | | | | ✅ | | |
| `b5f572c` | Replace Google API OAuth with rclone | | | | | ✅ | | ✅ |
| `179b802` | Non-interactive rclone config | | | | | ✅ | | |
| `076089c` | Bootstrap mode + override fix | | | | ✅ | | | ✅ |
| **`84333b8`** | **7-agent swarm: 14 fixes** | ✅ | ✅ | | ✅ | ✅ | ✅ | ✅ |

### Project phases

```mermaid
timeline
    title ME135 Project Evolution
    section Coursework
        Lab exercises : LabVIEW case structures, array averaging (commits 60d0f1a, a15d0fb)
    section Foundation
        Initial architecture : Full CV pipeline, ESP32 firmware, serial protocol, config, orchestrator (commit 0573d44)
    section Automation
        Doc agent swarm : 3-personality AI doc generation — Historian, Architect, Critic (commit ffb387f)
        Google Drive sync : Feature reports auto-sync to Drive via rclone (commits 5c494f9, b5f572c, 179b802)
        Bootstrap mode : Override features, --bootstrap flag for doc agent (commit 076089c)
    section Hardening
        14-proposal fix sweep : Security guards, ESP32 buffer fix, context managers, 46 tests, corrected docs (commit 84333b8)
```

**Key inflection point:** Commit `84333b8` marks the transition from "generating code" to "hardening code." Every prior commit added new capability; this one fixed 14 identified flaws without adding new features. The project now has its first test suite, security boundaries on agent tools, and resource-safe pipeline teardown — the hallmarks of production-readiness for a hardware demo.

### Code Health Summary

**Overall Grade: B**

This is a well-structured embedded CV project with clear separation of concerns (pipeline → serial → ESP32 firmware), good documentation, and thoughtful safety features (watchdogs, CRC, retry logic). The config-driven architecture and drop-in CPU/GPU pipeline pattern show solid design thinking.

**Strengths:** CRC-16 implementations match across Python and C++. Frame protocol is well-specified. Config is centralized. Background subtraction has multiple methods with sensible defaults.

**Critical gaps:** The serial ACK timeout is stored but never actually applied (SERIAL-01), both camera pipelines silently accept a missing device (CV-01), and the main loop leaks hardware resources on any exception (MAIN-01). The GPU pipeline breaks the API contract by omitting context manager support (GPU-01).

**Minor concerns:** Dead 1.4 KB buffer on ESP32, blocking `input()` preventing headless deployment, no write-conflict protection in the orchestrator.

Fix the 3 must-fix issues before any hardware integration testing.

---
## v4 — 2026-05-07 18:14 — `90363fc`

### What Changed

## Commit `90363fc` — Hardware Pivot: WS2812B → Waveshare RGB-Matrix-P2 64×64 (HUB75)

This is the **defining commit of the current sprint**: the display hardware was swapped from a custom-built 108×108 WS2812B addressable LED panel to an off-the-shelf **Waveshare RGB-Matrix-P2 64×64** HUB75 panel. No functional code was rewritten — instead, authoritative config was updated and every stale file got a prominent warning banner pointing to what must change next.

### Configuration & Context (the "source of truth" layer)

- **`config.yaml`** — `display.type` changed from `"ws2812b"` to `"hub75_waveshare_p2_64"`. Panel dimensions: 108×108 → **64×64** (4,096 LEDs, down from 11,664). Driver library: `ESP32-HUB75-MatrixPanel-DMA` replaces FastLED. `fps_target` raised from 10 → **60** because HUB75 DMA easily sustains it. Single `gpio_data_pin` removed — HUB75 needs 13 control GPIOs.
- **`config.yaml` processing section** — `output_width`/`output_height` changed from 400×300 to **64×64**, matching the panel's native resolution. This eliminates the intermediate downsample stage.
- **`orchestrator.py` PROJECT_CONTEXT** — The entire project description block now references the Waveshare panel, 512 bytes/frame payload, 921,600 bps baud, and `ESP32-HUB75-MatrixPanel-DMA`. This matters because every Claude agent spawned by the orchestrator inherits this context.
- **`doc_agent.py`** — Architect agent prompt updated: data-flow diagram now targets "64×64 Waveshare HUB75 LED panel" and "512 B/frame".
- **`PROJECT_README.md`** — One-liner, architecture overview, and ASCII data-flow diagram all rewritten: 400×300 / 15 KB → 64×64 / 512 B. Transport label changed from GPIO → HUB75.

### STALE Banners (the "debt marker" layer)

These files received prominent `⚠ STALE` banners at the top, explaining exactly what needs rewriting. No functional code was changed — the old logic still compiles/runs but targets the wrong hardware:

- **`esp32_main.cpp`** — Banner says: replace FastLED with `ESP32-HUB75-MatrixPanel-DMA`, change `MATRIX_COLS/ROWS` to 64, `FRAME_BYTES` to 512, map 13 HUB75 GPIOs. Marked **"DO NOT FLASH AS-IS"**.
- **`serial_protocol.py`** — Banner says: payload shrinks from 1,458 → 512 bytes, adjust `LEN_H/LEN_L` and CRC scope. Notes that Larry's pipeline already outputs 64×64 natively.
- **`PROTOCOL_SPEC.md`** — Banner outlines a v2.0 spec: 512-byte payload, ~518-byte total frame, 921,600 bps sufficient for 60+ fps. Old v1.0 spec body (15,000-byte frames at 2 Mbaud) left intact for reference.
- **`hardware_recommendation.md`** — Banner identifies 4 BOM rows to drop (WS2812B strip, 30A PSU, level shifter, inrush cap) and replace with the Waveshare panel + 5V/4A supply. Wiring diagram and power budget flagged for rewrite.
- **`cv_pipeline.py`** and **`gpu_accelerated.py`** — Both get banners noting their docstrings still say 400×300, but the config now feeds them 64×64. Actual resize call reads `out_w`/`out_h` from config, so **the pipeline already works at 64×64 without code changes** — only the docstrings and comments are stale.

### Earlier Commits in This Window

| Commit | Subsystem | What & Why |
|--------|-----------|------------|
| `1d051ee` | CV/Vision | Replaced `[` / `]` keyboard-driven confidence keys with a **trackbar slider** (`Conf %`). Why: continuous adjustment is faster during live tuning than discrete key presses. |
| `ac90ef9` | CV/Vision | Kyle **forked** `Larry/vision.py` into his own workspace for personal experiments. Establishes the Kyle/Larry branch convention. |
| `e78df3f` | Project governance | Added `CLAUDE.md` — the collaboration convention doc that tells Claude agents which workspace belongs to whom (Kyle vs. Larry). |
| `49bf44b` | CV + ESP32 | Larry's initial commit: vision code and optimized C++ code. The project's genesis. |

### Evolution Timeline

```mermaid
gitGraph
    commit id: "49bf44b" tag: "genesis" type: HIGHLIGHT
    commit id: "5a9c7ad" type: NORMAL
    commit id: "e78df3f"
    commit id: "ac90ef9"
    commit id: "a0389e2" type: REVERSE
    commit id: "b3727ee" type: REVERSE
    commit id: "a2b44d1" type: REVERSE
    commit id: "1d051ee"
    commit id: "dbb97ad" type: REVERSE
    commit id: "90363fc" tag: "HUB75-pivot" type: HIGHLIGHT
```

### Subsystem Touch Map

| Commit | CV Pipeline | Serial Protocol | ESP32 Firmware | Config | Docs / Governance | Vision (Kyle) |
|--------|:-----------:|:---------------:|:--------------:|:------:|:-----------------:|:-------------:|
| `49bf44b` — initial code | ✅ | | ✅ | | | |
| `5a9c7ad` — merge main | | | | | | |
| `e78df3f` — CLAUDE.md | | | | | ✅ | |
| `ac90ef9` — fork vision.py | | | | | | ✅ |
| `1d051ee` — trackbar slider | | | | | | ✅ |
| **`90363fc` — HUB75 pivot** | ✅ | ✅ | ✅ | ✅ | ✅ | |

> **Pattern:** The project started with Larry's CV + C++ foundation, Kyle forked the vision code for experimentation, and the HEAD commit is a sweeping **hardware-pivot bookkeeping pass** — updating every config surface while deliberately deferring the actual code rewrites behind STALE banners. This is a "declare the destination, mark the debt" strategy: the team can now grep for `⚠ STALE` to find every file that needs a rewrite before the next hardware integration milestone.

### Code Health Summary

**Overall Grade: D+**

The codebase demonstrates strong *architectural intent* — clean module boundaries, config-driven design, dual CPU/GPU pipelines, and a well-structured agent orchestration layer. Documentation is thorough.

However, **a hardware migration from 108×108 WS2812B to 64×64 HUB75 was only partially applied**, leaving the system in a broken state. `config.yaml` was updated to 64×64, but `serial_protocol.py` still hardcodes 108×108/400×300, meaning **every serial frame raises `ValueError`**. The ESP32 firmware still targets the wrong display hardware entirely. Protocol docs are self-contradictory (banners say 512B, body says 15KB).

Beyond the migration debt: camera open is never verified, the GPU pipeline lacks a safety guard present in the CPU path, and `SerialSender` has no context-manager cleanup. The orchestrator and doc-agent layers are in better shape but have minor subprocess fragility and a needless JSON roundtrip.

**Bottom line:** Fix the three must-fix dimension/hardware issues and this jumps to a B.

### Improvement Proposals

**[DOC-001] doc_agent.py git subprocess calls crash if git unavailable or no history** — 🟡 medium — nice-to-have

*Problem:* In `get_git_context()`, multiple `subprocess.run(['git', ...])` calls are made without try/except. If git is not installed, not in PATH, or the repo has <2 commits (making `HEAD~1` invalid), these calls will either raise `FileNotFoundError` or return error output silently. The `HEAD~1` reference is especially fragile on shallow clones or fresh repos. `get_changed_files()` in gdrive_sync.py has the same issue.

*Fix:* Wrap each git subprocess call in try/except (catching `FileNotFoundError` and `subprocess.SubprocessError`). For `HEAD~1`, first check commit count with `git rev-list --count HEAD` and fall back to `git diff --cached` or `git show --name-only HEAD` if only 1 commit exists. Return empty/fallback strings on failure rather than crashing.

---
