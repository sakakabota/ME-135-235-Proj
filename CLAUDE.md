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

- **Kyle's pipeline** (`agent_outputs/` in the sibling `ME135 Camera Processing` folder, integrated into `Kyle/`): PS3 Eye → OpenCV background subtraction (MOG2/KNN) → 400×300 binary matrix → bit-packed serial → ESP32 → WS2812B LED panel.
- **Larry's pipeline** (`Larry/vision.py`, `Larry/vision_fast.cpp`): YOLOv8 instance segmentation → 64×64 binary silhouette. Python via `ultralytics`, or C++ via OpenCV DNN reading exported ONNX. No serial/ESP32 transport — preview-only for now.

The two pipelines are different detection strategies for the same downstream goal. They are not (yet) integrated. Don't assume one supersedes the other without explicit instruction from Kyle.

## Documentation Agent

`Kyle/doc_agent.py` runs automatically pre-push via `.git/hooks/pre-push`. It generates evolution reports into `Kyle/docs/`. Don't disable the hook without asking Kyle.
