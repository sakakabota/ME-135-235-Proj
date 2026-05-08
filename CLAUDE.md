# ME-135-235-Proj — Claude Code Instructions

## Collaboration Convention (Kyle ↔ Larry)

This repo is shared between **Kyle** (`Kyle/`) and **Larry** (`Larry/`). Each person owns their own top-level folder. Respect that boundary — it's the entire reason the folders exist.

### Hard rules

1. **Never modify files inside `Larry/` directly.** That's his workspace. If Kyle wants to iterate on something Larry wrote (e.g. `Larry/vision.py`), copy it into `Kyle/` first and edit the copy:
   ```bash
   cp Larry/vision.py Kyle/vision/vision.py
   ```
   The original stays untouched. Same rule in reverse for Larry's files: nothing in `Kyle/` should be edited by a session working on Larry's behalf.

2. **Never commit directly to `main`.** Always work on a branch named `kyle/<topic>` (or `larry/<topic>`). Push the branch and let the other person see it before anything lands on `main`.

3. **If a change should be upstreamed into the other person's folder, open a Pull Request.** Don't push to `main` modifying someone else's folder. The PR description is the conversation: what changed, why, what was measured. Let the folder owner merge.

4. **Never rebase, force-push, or rewrite shared history.** If a bad commit lands on `main`, recover with `git revert`, not `git reset --hard` + force push. Larry/Kyle's local history must stay intact.

5. **Don't fork the repo to a personal GitHub account.** Both contributors have direct access to `sakakabota/ME-135-235-Proj`. A personal fork creates two sources of truth.

### Shared-space files

A few files at the repo root (`yolov8n-seg.pt`, `LabviewCamCode.vi`, `.gitignore`, this file) are genuinely shared. Edits to those are fine but should still go through a branch + brief commit message explaining the change. Don't churn shared assets without a reason.

### Commit messages as conversation

Treat each commit message as a note to the other person. Short, honest, descriptive of what was tried and what was observed. The git log is the primary async communication channel between Kyle and Larry on this project — don't waste it on "wip" or "update".

### When unsure, ask Kyle before acting

If a task is ambiguous about which folder to work in, or whether an edit should be a copy-into-Kyle vs. a PR-into-Larry, ask. Don't guess. The cost of a one-line clarifying question is tiny; the cost of a session that quietly rewrites Larry's code is large.

---

## Project Context

ME135/235 final project. Real-time human detection pipeline:

- **Kyle's pipeline** (`agent_outputs/` in the sibling `ME135 Camera Processing` folder, integrated into `Kyle/`): PS3 Eye → OpenCV background subtraction (MOG2/KNN) → binary matrix → bit-packed serial → ESP32 → 64×64 LED matrix.
- **Larry's pipeline** (`Larry/vision.py`, `Larry/vision_fast.cpp`): YOLOv8 instance segmentation → 64×64 binary silhouette. Python via `ultralytics`, or C++ via OpenCV DNN reading exported ONNX. No serial/ESP32 transport — preview-only for now.

The two pipelines are different detection strategies for the same downstream goal. They are not (yet) integrated. Don't assume one supersedes the other without explicit instruction from Kyle.

### Display hardware (current)

The output device is a **Waveshare RGB-Matrix-P2 64×64** LED matrix board (HUB75 interface, 2mm pitch, 128mm × 128mm, 4096 pixels). Reference: https://www.waveshare.com/wiki/RGB-Matrix-P2-64x64

Implications:
- Native frame size sent to the ESP32 is **64×64 = 4096 bits = 512 bytes/frame** bit-packed. Older pipeline docs that reference 400×300 CV output, 108×108 downsampled transmission, or ~15,000-byte payloads are stale and pre-date this hardware change.
- The ESP32 drives the panel over **HUB75**, not WS2812B. Use a HUB75 driver (e.g., `ESP32-HUB75-MatrixPanel-DMA`) — `FastLED` / NeoPixel paths in older `agent_outputs/` files are wrong for this hardware.
- **Larry's pipeline already outputs 64×64 natively**, so YOLO silhouettes can go to the panel without an extra resize. Kyle's MOG2 pipeline still needs a resize step from CV resolution down to 64×64 before bit-packing.
- Files known stale and needing rewrite: `Kyle/agent_outputs/esp32_main.cpp`, `Kyle/agent_outputs/serial_protocol.py` (downsample math + payload size), `Kyle/agent_outputs/hardware_recommendation.md` (BOM, wiring, power budget).

## Documentation Agent — Read This Before Pushing

`Kyle/doc_agent.py` runs automatically pre-push via `.git/hooks/pre-push`. It spawns 3 parallel Claude agents (historian/Opus, architect/Sonnet, critic/Opus) that read source files and call the Anthropic API. Generates evolution reports into `Kyle/docs/` and a SQLite history.

**⚠ Critical for any agent running `git push` in this repo:**

- The hook takes **30–90 seconds**. The critic may prompt interactively (`y/N`) for proposed fixes.
- A non-TTY agent shell will not see the hook's progress UI and will not be able to answer interactive prompts. From the agent's perspective, the push will appear to hang silently.
- **Do not retry or kill the push when it seems hung.** That is the hook running. Verify with `ps aux | grep -E "doc_agent|claude"`. Wait at least 2 minutes before suspecting a real issue.
- If an agent genuinely needs to push without the hook (rare), use `git push --no-verify` as an escape hatch. Don't make a habit of it.
- Don't disable the hook. Don't modify `Kyle/doc_agent.py` without asking Kyle.
