# ME135 | Hardware & Platform

> Evolution log — one section per commit.

---

## v1 — 2026-03-13 23:49 — `179b802`

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

---
## v2 — 2026-05-07 18:14 — `90363fc`

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

**[PROP-006] hardware_recommendation.md BOM rows need concrete rewrite** — 🟡 medium — must-fix

*Problem:* The STALE banner lists exactly which BOM rows to replace and what to substitute, but the actual BOM table below it still lists WS2812B strips, 30A PSU, level shifters, and inrush caps. A student ordering parts from this BOM will buy the wrong components.

*Fix:* Replace BOM rows 4, 5, 8, 9 with: Waveshare RGB-Matrix-P2 64×64, HUB75 IDC ribbon cable, 5V/4A bench supply. Update wiring diagram for 13 HUB75 GPIOs. Recalculate power budget for 4,096 LEDs (peak ~4A full white).

---
## v3 — 2026-05-07 18:20 — `9087b6c`

### What Changed

## Commit `9087b6c` — The End-to-End Hardware Pivot

This commit is the project's inflection point: the entire data path — from webcam to physical LEDs — was rewritten for the new Waveshare P2 64×64 HUB75 panel, replacing the never-deployed 108×108 WS2812B design that lived in `agent_outputs/`.

### CV Pipeline / Vision (`Kyle/vision/`)

- **`serial_protocol.py` (new, 165 lines):** Production replacement for the stale `agent_outputs/serial_protocol.py`. Key changes from the old version:
  - Panel resolution: **64×64** (was 108×108). Payload shrinks from 1,458 → **512 bytes**.
  - CRC-16/CCITT-FALSE kept identical; cross-verified with ESP32 side (`crc16_ccitt('123456789') = 0x29B1`).
  - Includes `pack_mask_64x64()` bit-pack helper and `SerialSender` class with framed 520-byte packets.
  - **Why it matters:** The old protocol couldn't talk to the new panel. This locks the wire format so both Python and C++ sides agree byte-for-byte.

- **`vision_send.py` (new, 224 lines):** Merges YOLO-based person segmentation (from `vision.py`) with live USB serial transport. Highlights:
  - Auto-detects `/dev/cu.usbserial-*` on macOS — no hardcoded port.
  - `--no-serial` flag lets you tune the CV pipeline without plugging in an ESP32.
  - Targets **~30 fps** over **1 Mbaud** (at 520 bytes/frame, serial TX takes ~4.2 ms — plenty of headroom).
  - **Why it matters:** Previously, vision and serial were separate modules glued by `main.py`. This collapses the stack into a single runnable script for the bench rig.

- **`requirements.txt` (new, 4 lines):** Pins `ultralytics`, `opencv-python`, `pyserial`, `numpy` — first time Kyle's vision folder has explicit deps.

### ESP32 Firmware (`Kyle/firmware/me135_led_pot/`)

- **`main.cpp` (new, 254 lines):** Complete rewrite of the display controller. Key architectural differences from the old `agent_outputs/esp32_main.cpp`:

  | Aspect | Old (`agent_outputs/`) | New (`firmware/`) |
  |--------|----------------------|-------------------|
  | Display | WS2812B via NeoPixel, 11,664 LEDs | HUB75E via ESP32-HUB75-MatrixPanel-DMA, 4,096 pixels |
  | Payload | 1,458 bytes (108×108) | 512 bytes (64×64) |
  | Baud | 2 Mbaud on HardwareSerial1 | 1 Mbaud on USB-CDC (`Serial`) |
  | Host | Jetson (UART GPIO 16/17) | Mac (USB serial) |
  | Color | Fixed white | **Pot-controlled white→red lerp** (EWMA-smoothed ADC on GPIO 34) |
  | RX model | Blocking `receiveFrame()` | **Non-blocking state machine** (`pollFrame()`) — won't stall display refresh |
  | Watchdog | Reset on timeout | Blanks panel after 5 s, recovers on next good frame |

  - E_PIN = GPIO 32, required for 1/32-scan 64-row panels — not present in the old code at all.
  - `setRxBufferSize(2048)` called **before** `Serial.begin()` (the commit message notes this was a Codex-caught bug, fixed pre-commit).

- **`platformio.ini` (new):** Targets `espressif32@6.5.0`, pulls `ESP32-HUB75-MatrixPanel-DMA@^3.0.11`, Adafruit GFX, and BusIO. Clean PlatformIO project — no manual library installs.

### Documentation (`Kyle/firmware/`)

- **`WIRING.md` (new, 124 lines):** Bench reference that didn't exist before. Covers:
  - Full 16-pin HUB75E ↔ ESP32 GPIO mapping table.
  - Power separation rules (panel on dedicated 5V/3A PSU, ESP32 on USB — **never share**).
  - GPIO 12 strapping-pin caveat with concrete fix (remap G2 to GPIO 18).
  - Optional 74HCT245 level-shifter guidance.
  - 9-step bring-up checklist and a troubleshooting table.
  - **Why it matters:** Hardware projects die when wiring knowledge lives only in someone's head. This is the "bus factor" document.

### Project Hygiene (`Kyle/.gitignore`)

- Added `*.pt` (model weights — fetched on first run, too large for git).
- Added `firmware/*/.pio/` and `firmware/*/.vscode/` (PlatformIO build artifacts).

### What Was NOT Touched

- `Larry/` — entirely separate workspace, untouched per collaboration convention.
- `Kyle/agent_outputs/` — kept as historical reference. Files now carry `⚠ STALE` banners (added in earlier commits `90363fc` and `1d051ee`).
- `Kyle/vision/vision.py` — the standalone YOLO viewer. `vision_send.py` forks its logic but doesn't modify the original.

---

### Earlier Commits in This Window

| Commit | Subsystem | What Changed |
|--------|-----------|-------------|
| `e78df3f` | Docs | Added `CLAUDE.md` — Kyle/Larry collaboration convention (workspace separation rules) |
| `ac90ef9` | Vision | Forked `Larry/vision.py` → `Kyle/vision/vision.py` for independent experiments |
| `1d051ee` | Vision | Replaced `[` / `]` keyboard confidence controls with a **CV2 trackbar slider** (better UX, no key-repeat issues) |
| `90363fc` | Docs | Switched project context to Waveshare 64×64 HUB75 — added `⚠ STALE` banners to all `agent_outputs/` files |

### Evolution Timeline

```mermaid
gitGraph
    commit id: "e78df3f" tag: "collab-convention" type: HIGHLIGHT
    commit id: "ac90ef9"
    commit id: "1d051ee"
    commit id: "90363fc"
    commit id: "9087b6c" tag: "end-to-end-v2" type: HIGHLIGHT
```

### Subsystem touch map

```mermaid
timeline
    title Project Evolution — Kyle's Workspace
    section Foundations
        e78df3f CLAUDE.md : Docs
            : Established Kyle/Larry workspace separation
            : Convention for co-development with AI
    section CV Pipeline Experiments
        ac90ef9 Fork vision.py : Vision
            : Forked Larry's YOLO pipeline into Kyle/vision/
            : Independent experiment sandbox
        1d051ee Conf trackbar : Vision
            : Replaced keyboard confidence controls with GUI slider
            : Better UX for tuning YOLO threshold
    section Hardware Pivot
        90363fc Context switch : Docs
            : Declared Waveshare P2 64x64 as target hardware
            : Marked all agent_outputs/ files as STALE
    section End-to-End Pipeline
        9087b6c Full stack : Vision + Serial + Firmware + Docs
            : serial_protocol.py — 512-byte CRC16 frames
            : vision_send.py — YOLO mask to USB at 30fps
            : main.cpp — ESP32 HUB75 DMA + pot lerp
            : WIRING.md — complete bench reference
```

### Architecture before and after

```mermaid
block-beta
    columns 3

    block:old["Agent Outputs (STALE)"]:3
        A["Jetson\n400×300 CV"] --> B["serial_protocol.py\n108×108 → 1458 B\n2 Mbaud UART"]
        B --> C["esp32_main.cpp\nWS2812B NeoPixel\n11,664 LEDs"]
    end

    space:3

    block:new["Kyle/ (ACTIVE)"]:3
        D["Mac Webcam\nYOLO v8 seg"] --> E["vision_send.py\n64×64 → 512 B\n1 Mbaud USB"]
        E --> F["main.cpp\nHUB75 DMA\n4,096 px + pot"]
    end
```

**The fundamental shift:** from a theoretical Jetson→WS2812B design (never physically built) to a working Mac→ESP32→HUB75 rig verified on the bench. Frame size dropped 65× (15,000 → 512 bytes), the display driver changed from bit-banged NeoPixel to DMA-driven HUB75, and a potentiometer adds real-time color control — the first interactive element in the project.

### Code Health Summary

**Overall Grade: C+**

The codebase demonstrates solid architectural thinking — clean separation of CV pipeline, serial transport, and firmware; config-driven design; graceful GPU/CPU fallback. The agent orchestrator and documentation system are well-engineered.

However, a **hardware migration from WS2812B 108×108 to HUB75 64×64 was only partially applied**. `config.yaml` was updated, but `serial_protocol.py` and `esp32_main.cpp` still carry the old dimensions, producing a **guaranteed runtime crash** (`ValueError` in `pack_matrix`) the moment a frame is sent. The firmware is entirely non-functional on the target hardware (wrong driver library, wrong GPIO mapping, wrong payload size).

Secondary concerns: no resource cleanup on exceptions in `main.py`, no camera-open validation, duplicated logic across CPU/GPU pipelines, and no cost guardrails on the 7-agent orchestrator.

The documentation quality is excellent — stale sections are clearly marked — but the code hasn't caught up.

### Improvement Proposals

**[SERIAL-002] serial_protocol.py CRC-16 is pure Python byte-at-a-time — bottleneck at 60 fps** — 🟢 low — nice-to-have

*Problem:* The `crc16_ccitt()` function iterates over every byte with 8 inner-loop iterations each. For 512-byte payloads at 60 fps, that's ~245K Python loop iterations per second. While individually tolerable, this runs in the hot frame loop alongside capture, CV processing, and serial I/O. Pure-Python loops are ~100× slower than C equivalents, adding unnecessary latency on an already-constrained Jetson.

*Fix:* Replace the manual loop with `crcmod.predefined.mkCrcFun('crc-ccitt-false')` (C-accelerated, available via `pip install crcmod`) or use a 256-entry lookup table. Either approach reduces CRC time from ~0.3 ms to <0.01 ms per frame. Add `crcmod` to `requirements.txt`.

---
## v4 — 2026-05-07 18:20 — `1ea86b2`

### What Changed

This window covers **6 meaningful commits** (plus 4 auto-generated doc reports) and one transformative hardware pivot. Changes grouped by subsystem:

### 🔧 Hardware & Configuration — The HUB75 Pivot (`90363fc`)

The single most consequential change: the display hardware was swapped from a custom 108×108 WS2812B addressable LED build to an off-the-shelf **Waveshare RGB-Matrix-P2 64×64 (HUB75)** panel.

- **`config.yaml`** — `display.type` flipped from `ws2812b` to `hub75_waveshare_p2_64`. Panel shrinks from 11,664 LEDs (108×108) to 4,096 (64×64). Driver library changes from FastLED to `ESP32-HUB75-MatrixPanel-DMA`. FPS target raised 10→60 (HUB75 DMA is fast). GPIO model changes from single data pin to 13 HUB75 control lines.
- **`config.yaml` processing section** — `output_width`/`output_height` updated from 400×300 to **64×64**, matching panel native resolution. This eliminates the intermediate downsample stage entirely.
- **Why it matters:** Every downstream module — serial protocol, ESP32 firmware, CV pipeline — inherits its dimensions from this config. The config is now correct, but the consuming code hasn't caught up yet.

### ⚠️ STALE Banners — Debt Declared, Not Resolved (`90363fc`)

Rather than rewriting code mid-sprint, the team placed prominent `⚠ STALE` banners on every file that references the old hardware:

- **`esp32_main.cpp`** — Banner says: replace FastLED with HUB75 DMA, change dimensions to 64×64, remap 13 GPIOs. Marked **"DO NOT FLASH AS-IS"**.
- **`serial_protocol.py`** — Still hardcodes `PANEL_ROWS=108`, `PANEL_COLS=108`, `PAYLOAD_BYTES=1458`. Banner says: payload drops to 512 bytes. Current code will **raise `ValueError`** on any 64×64 input.
- **`cv_pipeline.py`** and **`gpu_accelerated.py`** — Docstrings say 400×300 but the actual resize reads `out_w`/`out_h` from config dynamically — **these work at 64×64 already**, only comments are stale.
- **`PROTOCOL_SPEC.md`** and **`hardware_recommendation.md`** — Old spec body retained for reference; banners outline the v2.0 numbers.

> **Strategy: "Declare the destination, mark the debt."** The team can `grep -r "STALE"` to find every file needing a rewrite before the next hardware integration milestone.

### 🤖 Agent Orchestration & Governance

- **`orchestrator.py` `PROJECT_CONTEXT`** (`90363fc`) — The master prompt block that every spawned Claude agent inherits was rewritten to reference the Waveshare panel, 512 B/frame, 921,600 bps, and `ESP32-HUB75-MatrixPanel-DMA`. This is critical because all 7 parallel agents derive their understanding of the hardware from this string.
- **`doc_agent.py`** (`90363fc`) — Architect agent prompt updated: data-flow diagram target is now "64×64 Waveshare HUB75 LED panel" and "512 B/frame".
- **`PROJECT_README.md`** (`90363fc`) — Architecture overview and ASCII data-flow diagram rewritten: 400×300 / 15 KB → 64×64 / 512 B.
- **`CLAUDE.md`** (`e78df3f`) — New governance doc defining the Kyle/Larry workspace convention so Claude agents know who owns what.

### 👁️ Computer Vision / Kyle's Vision Fork

- **`ac90ef9`** — Kyle **forked** `Larry/vision.py` into his own workspace. This establishes the two-workspace pattern: Larry's code is the production baseline, Kyle's is the experimentation branch.
- **`1d051ee`** — In Kyle's vision fork, replaced discrete `[` / `]` keyboard shortcuts for adjusting detection confidence with a **continuous trackbar slider** (`Conf %`). Why: live-tuning a threshold with a slider is far faster than tapping keys during a camera session.

### 📄 Documentation System (`1ea86b2`, HEAD)

- Auto-generated 926 lines of feature documentation across 7 feature files (`ME135_Agent_Swarm.md`, `ME135_Computer_Vision_Pipeline.md`, `ME135_ESP32_Firmware.md`, etc.) plus a timestamped evolution report (`report_2026-05-07_1814.md`).
- `docs/README.md` index updated with a link to the new report.
- Each feature doc now includes a v3 section documenting the HUB75 pivot, a subsystem touch map, and a code health grade (D+ — strong architecture, broken by incomplete migration).

### Evolution Timeline

```mermaid
gitGraph
    commit id: "5a9c7ad – merge main" type: NORMAL
    commit id: "e78df3f – CLAUDE.md governance" type: NORMAL
    commit id: "ac90ef9 – fork vision.py" type: NORMAL
    commit id: "a0389e2 – auto-docs" type: REVERSE
    commit id: "b3727ee – auto-docs" type: REVERSE
    commit id: "a2b44d1 – auto-docs" type: REVERSE
    commit id: "1d051ee – trackbar slider" type: NORMAL
    commit id: "dbb97ad – auto-docs" type: REVERSE
    commit id: "90363fc – HUB75 pivot" tag: "hardware-swap" type: HIGHLIGHT
    commit id: "1ea86b2 – evolution report" tag: "HEAD" type: NORMAL
```

### Subsystem Touch Map

| Commit | CV Pipeline | Serial Protocol | ESP32 Firmware | Config | Docs / Governance | Vision (Kyle) |
|--------|:-----------:|:---------------:|:--------------:|:------:|:-----------------:|:-------------:|
| `5a9c7ad` — merge main | | | | | | |
| `e78df3f` — CLAUDE.md | | | | | ✅ | |
| `ac90ef9` — fork vision.py | | | | | | ✅ |
| `1d051ee` — trackbar slider | | | | | | ✅ |
| **`90363fc` — HUB75 pivot** | ✅ | ✅ | ✅ | ✅ | ✅ | |
| `1ea86b2` — evolution report | | | | | ✅ | |

### Trajectory Reading

The project follows a clear three-act arc so far:

1. **Genesis** — Larry's initial CV + ESP32 code established the pipeline end-to-end (pre-window).
2. **Fork & Tune** — Kyle branched the vision code for independent experimentation, added the trackbar slider for faster live tuning, and the team codified governance rules (`CLAUDE.md`).
3. **Hardware Pivot** — The display hardware changed from WS2812B to HUB75. Config was updated authoritatively; all stale code was marked but not yet rewritten.

**What comes next:** The STALE banners are a to-do list. The serial protocol (`serial_protocol.py`) and ESP32 firmware (`esp32_main.cpp`) need dimension/driver rewrites before any hardware integration testing can proceed. The CV pipeline already works at 64×64 thanks to config-driven resizing — it's the furthest along.

### System Architecture

```mermaid
flowchart TD
    subgraph CAM_HW["📷 PS3 Eye Camera"]
        CAM["Sony PS3 Eye<br/>640×480 @ 60 fps<br/>USB 2.0 · Driver: gspca_ov534"]
    end

    subgraph JETSON["🖥️ NVIDIA Jetson Orin Nano Super · JetPack 6 · CUDA 12.2 · 8 GB LPDDR5"]
        YAML["config.yaml<br/>─────────────────<br/>SINGLE SOURCE OF TRUTH<br/>baud: 2,000,000<br/>output: 64×64<br/>use_gpu: true<br/>watchdog: 5 s"]

        MAIN["main.py · Orchestrator<br/>─────────────────<br/>• SIGINT / SIGTERM handler<br/>• Watchdog 5 s<br/>• FPS limiter @ 60 fps target<br/>• Safety: max 10 serial errors"]

        subgraph CV_PATH["Computer Vision Pipeline"]
            GPU["gpu_accelerated.py<br/>GPUPipeline<br/>────────────────────<br/>🟢 GPU — 1024 Ampere CUDA cores<br/>cv2.cuda.MOG2 background sub<br/>CUDA Gaussian blur<br/>CUDA resize → 64×64<br/>~2 ms / frame"]

            CPU["cv_pipeline.py<br/>CVPipeline<br/>────────────────────<br/>🟡 CPU fallback<br/>MOG2 / KNN / static_median<br/>cv2 morphological cleanup<br/>cv2 resize → 64×64<br/>~8 ms / frame"]
        end

        SER["serial_protocol.py · SerialSender<br/>──────────────────────────────<br/>np.packbits → 512 B payload<br/>CRC-16/CCITT-FALSE (poly 0x1021)<br/>Packet: 0xAA 0x55 LEN_H LEN_L PAYLOAD CRC 0x55 0xAA<br/>Total: 520 B / frame<br/>Retry: up to 3× on NAK<br/>ACK timeout: 50 ms"]
    end

    subgraph ESP_HW["⚡ ESP32-DevKitC · Xtensa LX6 240 MHz · Dual-core"]
        FW["esp32_main.cpp<br/>──────────────────────────────<br/>HardwareSerial1 · GPIO 16 RX / 17 TX<br/>RX buffer: 4 KB<br/>Frame sync: detect 0xAA 0x55<br/>CRC-16 verify over 512 B payload<br/>ACK 0x06 · NAK 0x15<br/>Hardware watchdog: 5 s"]

        RENDER["updateDisplay()<br/>──────────────<br/>Unpack bits MSB-first<br/>Map pixel → HUB75 DMA<br/>White = human · Black = bg"]
    end

    subgraph PANEL_HW["💡 Waveshare RGB-Matrix-P2 · 64×64 · HUB75"]
        PANEL["4,096 RGB LEDs · 2 mm pitch<br/>128×128 mm physical size<br/>Driver: ESP32-HUB75-MatrixPanel-DMA<br/>13-pin HUB75 IDC<br/>Brightness: 128/255 PWM"]
    end

    LABVIEW["LabVIEW IoT Hub<br/>(optional · TCP · port 5020)<br/>Heartbeat every 2 s"]

    %% Data paths with sizes/rates
    CAM -- "USB 2.0 · 640×480 BGR<br/>~921,600 B/frame · 60 fps<br/>~55 MB/s peak" --> MAIN
    YAML -- "parsed params dict" --> MAIN

    MAIN -- "use_gpu=true<br/>CUDA_AVAILABLE=True" --> GPU
    MAIN -. "fallback only<br/>use_gpu=false or no CUDA" .-> CPU

    GPU -- "64×64 uint8<br/>binary {0,1}<br/>4,096 B" --> SER
    CPU -- "64×64 uint8<br/>binary {0,1}<br/>4,096 B" --> SER

    SER -- "UART 3.3 V · 8N1<br/>2,000,000 baud<br/>520 B / frame<br/>TX time ≈ 2.6 ms" --> FW
    FW -- "1 B · ACK 0x06 / NAK 0x15<br/>within 50 ms" --> SER

    FW --> RENDER
    RENDER -- "HUB75 · 13-pin IDC<br/>DMA · 60+ fps capable" --> PANEL

    FW -. "TCP (optional / future)<br/>heartbeat + status" .-> LABVIEW
```

> **Key numbers at a glance**
>
> | Stage | Data Size | Rate / Latency |
> |---|---|---|
> | Camera capture | 921,600 B/frame (BGR) | 60 fps · ~55 MB/s |
> | GPU processing | 307,200 B (grayscale) | ~2 ms/frame |
> | CPU processing | 307,200 B (grayscale) | ~8 ms/frame |
> | Binary matrix | 4,096 B (64×64 uint8) | post-threshold |
> | Bit-packed payload | **512 B** | `np.packbits` |
> | Full UART packet | **520 B** | header(4) + payload(512) + CRC(2) + footer(2) |
> | UART TX time | 520 B @ 2 Mbaud | **≈ 2.6 ms** |
> | ACK window | 1 B | ≤ 50 ms timeout |

> ⚠️ **Staleness note:** `esp32_main.cpp` and `serial_protocol.py` still hardcode `PAYLOAD_BYTES=1458` (108×108 legacy WS2812B panel). `config.yaml` and `gpu_accelerated.py` are updated to 64×64 / 512 B. See Improvement Proposals below.

### Data Flow

```mermaid
sequenceDiagram
    participant CAM  as 📷 PS3 Eye Camera
    participant UMEM as Jetson CPU RAM
    participant GMEM as Jetson GPU VRAM<br/>(CUDA 12.2 · Ampere)
    participant PACK as serial_protocol.py<br/>(bit-pack + frame)
    participant UART as UART Bus<br/>(2,000,000 baud · 3.3 V)
    participant ESP  as ESP32-DevKitC<br/>(HardwareSerial1)
    participant HUB  as HUB75 Panel<br/>(64×64 · DMA)

    Note over CAM: Exposure window ≈ 16.7 ms (60 fps)

    CAM  ->>  UMEM: USB 2.0 DMA transfer<br/>640×480 YUYV → BGR decode<br/>921,600 B · ~1 ms

    UMEM ->>  GMEM: cv2.cuda GpuMat.upload()<br/>640×480 BGR · 921,600 B<br/>PCIe/shared mem · ~0.5 ms

    GMEM ->>  GMEM: cvtColor BGR→Gray<br/>640×480 · 307,200 B · ~0.3 ms

    GMEM ->>  GMEM: GaussianFilter 5×5 (CUDA)<br/>307,200 B · ~0.4 ms

    GMEM ->>  GMEM: MOG2 BackgroundSubtractor (CUDA)<br/>fg_mask · 307,200 B · learningRate=0.002 · ~0.5 ms

    Note over GMEM: Sanity check: if >60% foreground → stale model warning

    GMEM ->>  GMEM: MORPH_OPEN + MORPH_CLOSE (CUDA)<br/>ellipse kernel 5×5 · remove noise · ~0.3 ms

    GMEM ->>  GMEM: cuda.resize → 64×64<br/>INTER_NEAREST · 4,096 B · ~0.1 ms

    GMEM ->>  UMEM: GpuMat.download()<br/>binary matrix 64×64 uint8 · 4,096 B · ~0.2 ms

    Note over UMEM: Total GPU path ≈ 2 ms<br/>(CPU path ≈ 8 ms — same steps, no upload/download)

    UMEM ->>  PACK: binary_matrix (300,400) or (64,64)<br/>4,096 B ndarray

    PACK ->>  PACK: np.packbits(flatten, bitorder='big')<br/>4,096 B → 512 B · < 0.1 ms

    PACK ->>  PACK: crc16_ccitt(payload)<br/>CRC-16/CCITT-FALSE over 512 B<br/>poly=0x1021 · init=0xFFFF · ~0.1 ms

    PACK ->>  PACK: Build packet<br/>[0xAA][0x55] + LEN(2B) + PAYLOAD(512B)<br/>+ CRC(2B) + [0x55][0xAA]<br/>= 520 B total

    PACK ->>  UART: serial.write(520 B)<br/>pyserial · blocking write

    Note over UART: 520 B × 10 bits/B ÷ 2,000,000 baud<br/>= 2.60 ms transmission time

    UART ->>  ESP: 520 B frame arrives<br/>4 KB HardwareSerial RX buffer

    ESP  ->>  ESP: Sync scan: wait for 0xAA 0x55

    ESP  ->>  ESP: Read LEN_H + LEN_L (2 B)<br/>Validate == 512

    ESP  ->>  ESP: Buffer 512 B payload<br/>chunked readBytes loop

    ESP  ->>  ESP: Read CRC(2B) + 0x55 0xAA(2B)

    ESP  ->>  ESP: crc16_ccitt(payload, 512)<br/>Compare vs received CRC · ~0.1 ms

    alt CRC OK
        ESP  ->>  UART: ACK 0x06 · 1 B · ~0.04 ms
        UART ->>  PACK: ACK received ✓<br/>frames_acked++
        ESP  ->>  HUB: updateDisplay()<br/>Unpack bits MSB-first<br/>4,096 pixel colors · DMA push · ~1 ms
        HUB  ->>  HUB: Pixel visible on panel<br/>White (255,255,255) = human<br/>Black (0,0,0) = background
    else CRC FAIL
        ESP  ->>  UART: NAK 0x15 · 1 B
        UART ->>  PACK: NAK → retry attempt (max 3×)<br/>Retransmit same 520 B packet
    end

    Note over CAM, HUB: End-to-end latency (GPU path, excl. camera exposure)<br/>≈ 1 + 0.5 + 2 + 0.1 + 2.6 + 0.1 + 1 ≈ 7–8 ms / frame
```

> **Byte-count ledger — one frame**
>
> | Step | Bytes in | Transform | Bytes out |
> |---|---:|---|---:|
> | Camera raw (YUYV) | 614,400 | BGR decode | **921,600** |
> | Grayscale | 921,600 | cvtColor | **307,200** |
> | Foreground mask | 307,200 | MOG2 + morph | **307,200** |
> | Resized binary | 307,200 | resize 64×64 | **4,096** |
> | Bit-packed payload | 4,096 | `np.packbits` | **512** |
> | UART packet | 512 | frame wrap | **520** |
> | Panel pixels | 520 | `unpackbits` | **4,096** RGB values |
>
> **Compression ratio camera → panel: 921,600 B → 512 B = 1,800× reduction**

### Module Dependency Graph

```mermaid
graph LR
    %% ── Entry Point ──────────────────────────────────────────────
    subgraph ENTRY["🚀 Entry Point"]
        MAIN["main.py<br/>─────────────<br/>load_config()<br/>validate_config()<br/>_signal_handler()<br/>main()"]
    end

    %% ── CV Pipeline Modules ──────────────────────────────────────
    subgraph CV_MODS["🔬 Computer Vision  [agent_outputs/]"]
        GPU["gpu_accelerated.py<br/>─────────────────────<br/>class GPUPipeline<br/>  __init__(config)<br/>  calibrate() → None<br/>  process_frame() → tuple<br/>  release() → None<br/>const CUDA_AVAILABLE: bool"]

        CPU["cv_pipeline.py<br/>─────────────────────<br/>class CVPipeline<br/>  __init__(config)<br/>  calibrate() → None<br/>  process_frame() → tuple<br/>  release() → None<br/>context manager __enter__/__exit__"]
    end

    %% ── Serial / Comms ───────────────────────────────────────────
    subgraph COMM_MODS["📡 Serial Protocol  [agent_outputs/]"]
        SER["serial_protocol.py<br/>─────────────────────<br/>class SerialSender<br/>  __init__(config)<br/>  send_frame(matrix) → bool<br/>  close() → None<br/>fn crc16_ccitt(data) → int<br/>fn pack_matrix(matrix) → bytes<br/>fn unpack_matrix(data) → ndarray<br/>fn downsample_to_panel(m) → ndarray"]
    end

    %% ── Config ───────────────────────────────────────────────────
    subgraph CFG_MOD["⚙️ Configuration"]
        YAML["config.yaml<br/>─────────────<br/>camera · calibration<br/>processing · serial<br/>display · safety<br/>labview"]
    end

    %% ── Third-Party / Stdlib ─────────────────────────────────────
    subgraph EXT["📦 External Libraries"]
        CV2_CUDA["cv2.cuda<br/>(OpenCV CUDA)<br/>GpuMat · MOG2<br/>GaussianFilter<br/>MorphologyFilter"]
        CV2_CPU["cv2<br/>(OpenCV CPU)<br/>VideoCapture<br/>BackgroundSubtractor<br/>morphologyEx"]
        NP["numpy<br/>ndarray · packbits<br/>unpackbits · median"]
        PYSER["pyserial<br/>serial.Serial"]
        PYYAML["pyyaml<br/>yaml.safe_load"]
        STRUCT["struct (stdlib)<br/>pack / unpack"]
        SIGNAL["signal (stdlib)"]
        ARGPARSE["argparse (stdlib)"]
    end

    %% ── Infrastructure Agents ────────────────────────────────────
    subgraph INFRA["🤖 Agent Infrastructure"]
        ORCH["orchestrator.py<br/>─────────────────<br/>7× parallel async agents<br/>run_agent() loop<br/>execute_tool() dispatch<br/>MODEL_ARCHITECT: opus<br/>MODEL_CODER: sonnet"]

        DOC["doc_agent.py<br/>─────────────────<br/>Historian + Architect + Critic<br/>db_connect() · SQLite<br/>collect_project_files()<br/>get_git_context()"]

        GSYNC["gdrive_sync.py<br/>─────────────────<br/>detect_features()<br/>get_changed_files()<br/>sync_features_to_drive()<br/>compose_feature_section()"]

        GSET["gdrive_setup.py<br/>─────────────────<br/>rclone OAuth flow<br/>Drive folder create<br/>test sync verify"]
    end

    subgraph INFRA_EXT["📦 Infra External"]
        ANT["anthropic SDK<br/>AsyncAnthropic<br/>claude-opus-4-6<br/>claude-sonnet-4-6"]
        RCLONE["rclone<br/>(subprocess)<br/>me135drive remote"]
        SQLITE["sqlite3 (stdlib)<br/>proposals · reports"]
        GIT["git<br/>(subprocess)<br/>diff · log · show"]
    end

    %% ── Firmware (non-Python) ────────────────────────────────────
    subgraph FW_MOD["⚡ ESP32 Firmware  [agent_outputs/]"]
        CPP["esp32_main.cpp<br/>─────────────────<br/>receiveFrame() → RxResult<br/>updateDisplay() → void<br/>crc16_ccitt() → uint16_t<br/>setup() · loop()"]

        PIOINI["platformio.ini<br/>─────────────<br/>platform: espressif32<br/>board: esp32dev<br/>lib: Adafruit NeoPixel"]

        NEOP["Adafruit NeoPixel lib<br/>(⚠️ stale — should be<br/>ESP32-HUB75-MatrixPanel-DMA)"]
    end

    %% ═══════════════════════════════════════════════════════════
    %% Edges — main.py imports
    %% ═══════════════════════════════════════════════════════════
    MAIN -->|"from gpu_accelerated import\nGPUPipeline, CUDA_AVAILABLE"| GPU
    MAIN -->|"from cv_pipeline import\nCVPipeline"| CPU
    MAIN -->|"from serial_protocol import\nSerialSender"| SER
    MAIN -->|"import yaml"| PYYAML
    MAIN -->|"import cv2\n(preview window)"| CV2_CPU
    MAIN -->|"import numpy as np"| NP
    MAIN -->|"import signal, argparse"| SIGNAL
    MAIN -.->|"reads at startup"| YAML

    %% gpu_accelerated.py imports
    GPU -->|"import cv2.cuda"| CV2_CUDA
    GPU -->|"import numpy as np"| NP
    GPU -.->|"fallback reference\nCVPipeline used if CUDA absent"| CPU

    %% cv_pipeline.py imports
    CPU -->|"import cv2"| CV2_CPU
    CPU -->|"import numpy as np"| NP

    %% serial_protocol.py imports
    SER -->|"import serial"| PYSER
    SER -->|"import numpy as np"| NP
    SER -->|"import struct"| STRUCT
    SER -->|"import cv2\n(resize in downsample)"| CV2_CPU

    %% Firmware
    CPP -->|"#include"| NEOP
    PIOINI -->|"lib_deps"| NEOP

    %% Infrastructure imports
    ORCH -->|"import anthropic"| ANT
    DOC  -->|"import anthropic"| ANT
    DOC  -->|"from gdrive_sync import"| GSYNC
    DOC  -->|"import sqlite3"| SQLITE
    DOC  -->|"subprocess git"| GIT
    GSYNC -->|"subprocess rclone"| RCLONE
    GSET  -->|"subprocess rclone"| RCLONE

    %% Class interface boundary annotation
    GPU -.->|"⟵ identical public API ⟶<br/>calibrate() · process_frame() · release()"| CPU

    %% Styling
    classDef entryStyle fill:#1e3a5f,stroke:#4a90d9,color:#fff
    classDef cvStyle fill:#1a3d2b,stroke:#4caf50,color:#fff
    classDef commStyle fill:#3d2b1a,stroke:#ff9800,color:#fff
    classDef cfgStyle fill:#3d1a3d,stroke:#ce93d8,color:#fff
    classDef extStyle fill:#2a2a2a,stroke:#888,color:#ccc
    classDef infraStyle fill:#1a2a3d,stroke:#90caf9,color:#fff
    classDef fwStyle fill:#3d1a1a,stroke:#ef9a9a,color:#fff
    classDef staleStyle fill:#5c2d00,stroke:#ff6d00,color:#fff

    class MAIN entryStyle
    class GPU,CPU cvStyle
    class SER commStyle
    class YAML cfgStyle
    class CV2_CUDA,CV2_CPU,NP,PYSER,PYYAML,STRUCT,SIGNAL,ARGPARSE extStyle
    class ORCH,DOC,GSYNC,GSET infraStyle
    class ANT,RCLONE,SQLITE,GIT extStyle
    class CPP,PIOINI fwStyle
    class NEOP staleStyle
```

> **Interface contract — CV pipeline duck-typing**
>
> `main.py` selects between `GPUPipeline` and `CVPipeline` purely at runtime.
> Both expose an **identical public API**:
>
> ```
> pipeline = GPUPipeline(config)  # or CVPipeline(config)
> pipeline.calibrate()            # blocking — build background model
> binary, raw = pipeline.process_frame()  # returns (64×64 uint8, BGR frame)
> pipeline.release()              # free camera + GPU resources
> ```
>
> **`serial_protocol.py` public surface used by `main.py`:**
> ```
> sender = SerialSender(config)
> ok: bool = sender.send_frame(binary_matrix)   # packs + sends + waits ACK
> sender.close()
> ```
>
> 🟠 **`Adafruit NeoPixel`** dependency in `platformio.ini` / `esp32_main.cpp` is
> stale — the correct library for the HUB75 panel is `ESP32-HUB75-MatrixPanel-DMA`.

### Code Health Summary

**Overall Grade: C−**

The codebase demonstrates strong architectural intent — clean module separation, config-driven design, context managers, safety watchdogs, and a well-documented serial protocol. However, **the system cannot actually run end-to-end**. A hardware migration from 108×108 WS2812B to 64×64 HUB75 was partially applied: `config.yaml` was updated, but `serial_protocol.py` and `esp32_main.cpp` still hardcode the old 108×108 dimensions with the wrong driver library. This means `pack_matrix()` raises `ValueError` on every frame, and the ESP32 firmware targets nonexistent hardware.

Additionally, `min_contour_area=500` will filter out most human detections at 64×64 resolution. Resource cleanup in `main.py` is not exception-safe. Subprocess calls throughout lack timeouts, risking indefinite hangs.

**Positive notes:** CRC matches both sides, CV pipeline API is cleanly swappable (CPU/GPU), git-driven doc generation is clever, and config validation exists. Once the dimension mismatch is fixed, this is close to a working system.

### Improvement Proposals

**[ARCH-004] hardware_recommendation.md BOM rows 4,5,8,9 reference WS2812B/30A PSU/level-shifter — all obsolete** — 🟢 low — must-fix

*Problem:* The Bill of Materials still lists WS2812B LED panel, 5V/30A PSU (for 11,664 LEDs at 60 mA each), 3.3→5V level shifter, 1000 µF inrush cap, and 470 Ω data resistor. The HUB75 panel requires none of these: it uses 13 TTL-level GPIO lines (no shifter), draws ≤4 A peak, and has its own power connector.

*Fix:* Replace BOM rows 4,5,8,9,10 with: Waveshare RGB-Matrix-P2 64×64 ($25), HUB75 IDC ribbon cable ($3), 5V/5A bench supply ($15). Update power budget table: 4,096 LEDs peak ≈ 4A; typical silhouette draw <1A. Remove level-shifter and cap rows. Update wiring diagram for 13-GPIO HUB75 instead of GPIO 13 single-pin.

---
