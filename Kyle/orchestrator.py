#!/usr/bin/env python3
"""
ME135 Camera Processing — Claude Agent Swarm Orchestrator
UC Berkeley | Spring 2026

Spawns 7 specialized Claude agents in parallel, each self-assigned the most
relevant skills, to tackle every subsystem of the ME135 project:

  PS3 Eye Camera → Calibration → Background Subtraction → GPU Processing
  → 64×64 Binary Matrix → ESP32 Serial → Waveshare RGB-Matrix-P2 (HUB75)

Usage:
    python orchestrator.py
    python orchestrator.py --notebooks "notebook content here..."  # inject NotebookLM context

Outputs land in ./agent_outputs/
"""

import asyncio
import argparse
import json
import os
import textwrap
from pathlib import Path
import anthropic

# ─── Config ───────────────────────────────────────────────────────────────────

OUTPUT_DIR = Path(__file__).parent / "agent_outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

# Opus for architects/planners, Sonnet for focused code writers
MODEL_ARCHITECT = "claude-opus-4-6"
MODEL_CODER     = "claude-sonnet-4-6"

PROJECT_CONTEXT = """
ME135 Camera Processing Project — UC Berkeley (Spring 2026)
===========================================================
Goal:
  Build a real-time human detection system using a PS3 Eye Camera that produces
  a 64×64 binary pixel matrix sent over serial to an ESP32, which drives a
  Waveshare RGB-Matrix-P2 64×64 LED panel (HUB75 interface, 2mm pitch).
  Reference: https://www.waveshare.com/wiki/RGB-Matrix-P2-64x64

Hardware stack:
  - Camera:     Sony PS3 Eye (USB, 640×480 @ 60fps, Linux driver: gspca_ov534)
  - Host unit:  NVIDIA Jetson (Nano / Orin) OR ESP32 (for lightweight variant)
  - GPU:        Jetson onboard CUDA cores (preferred for image processing)
  - MCU:        ESP32 (receives matrix, drives panel via HUB75)
  - Display:    Waveshare RGB-Matrix-P2 64×64 (HUB75, 4096 RGB LEDs, 128×128 mm)
                Driver library: ESP32-HUB75-MatrixPanel-DMA (NOT FastLED/WS2812B)

Course context (from ME135 syllabus — MECENG 135, George Anwar):
  - 30% grade: 3-5 lab programming exercises; 60%: final project demo + presentation
  - Required deliverables: CAD schematics, circuit diagrams, software flowcharts,
    V&V matrix (verification = bench tests; validation = operational environment)
  - Safety: risk assessment required, hardware failsafes or software watchdog timers

System architecture (CLARIFIED):
  - LabVIEW role: IoT hub / central dashboard — receives data from ESP32 and other
    devices over the network, displays state, sends commands. NOT the CV processor.
  - CV processing: Python + GPU (preferred) OR LabVIEW — decision TBD based on
    performance benchmarks and available hardware
  - ESP32: receives binary matrix from the processing unit, drives the display,
    AND reports status back to LabVIEW hub

LabVIEW IoT hub best practices (from LabVIEW Expert notebook):
  - Producer/Consumer pattern: separate network receive loop from display/command loop
  - Use Queues (FIFO) between loops for reliable inter-process data
  - NI DSC module or shared variables for IoT device communication
  - Separate UI from execution logic (MVC pattern)
  - Hardware-timed loops for precise DAQ if sensors connect directly to NI hardware

Google Antigravity agent development best practices:
  - Spec-Driven Development: Task List → Implementation Plan → Walkthrough artifacts
  - Agent Manager orchestrates parallel async agents across workspaces
  - Skills system: 3-tier customization (Rules → Skills → Context)
  - Agents communicate progress via structured artifacts, not raw logs
  - Browser subagent (Gemini Computer Use) validates running apps autonomously

Software constraints:
  - Language:  Python (preferred) or LabVIEW
  - CV library: OpenCV (with CUDA backend on Jetson)
  - Output:    64×64 np.ndarray of uint8 (0 = background, 1 = human) — matches panel native res
  - Protocol:  Serial UART — bit-packed matrix at panel resolution.
               64×64 = 4,096 bits = 512 bytes/frame payload + framing/CRC overhead.
               At 921600 bps a 520-byte frame transmits in ~5.6 ms → comfortable headroom for 60+ fps.

Algorithm:
  1. Record a short calibration video (empty room, no humans)
  2. Build background model from calibration frames
  3. For each live frame: subtract background, threshold, resize to 64×64 (panel native)
  4. Produce binary matrix; transmit to ESP32
"""

# ─── Tool definitions ─────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "write_file",
        "description": (
            "Write or overwrite a file inside the agent_outputs directory. "
            "Use this to save your final code, documentation, or specifications."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Filename only (no path). E.g. 'cv_pipeline.py'"
                },
                "content": {
                    "type": "string",
                    "description": "Full file content to write."
                }
            },
            "required": ["filename", "content"],
            "additionalProperties": False
        }
    },
    {
        "name": "read_file",
        "description": (
            "Read a file previously written by another agent in agent_outputs. "
            "Use to check existing work before writing your own."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Filename to read from agent_outputs/"
                }
            },
            "required": ["filename"],
            "additionalProperties": False
        }
    }
]

def execute_tool(name: str, tool_input: dict) -> str:
    if name == "write_file":
        filename = tool_input["filename"]
        if Path(filename).name != filename:
            return f"ERROR: Invalid filename '{filename}' — path traversal not allowed."
        path = OUTPUT_DIR / filename
        path.write_text(tool_input["content"], encoding="utf-8")
        return f"✓ Written: {path}"
    elif name == "read_file":
        filename = tool_input["filename"]
        if Path(filename).name != filename:
            return f"ERROR: Invalid filename '{filename}' — path traversal not allowed."
        path = OUTPUT_DIR / filename
        if path.exists():
            return path.read_text(encoding="utf-8")
        return f"File not found: {filename}"
    return f"Unknown tool: {name}"

# ─── Agent runner ─────────────────────────────────────────────────────────────

async def run_agent(
    client: anthropic.AsyncAnthropic,
    agent_id: str,
    system_prompt: str,
    task_prompt: str,
    model: str = MODEL_CODER,
    max_turns: int = 12,
) -> dict:
    """
    Runs a single agent with an agentic tool-use loop.
    Returns when stop_reason == 'end_turn' or max_turns exceeded.
    """
    print(f"  ▶ [{agent_id}] started")
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

        # Append assistant turn
        messages.append({"role": "assistant", "content": response.content})

        # Collect text
        for block in response.content:
            if hasattr(block, "text"):
                final_text = block.text

        if response.stop_reason == "end_turn":
            break

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = execute_tool(block.name, json.loads(json.dumps(block.input)))
                    print(f"    ⚙ [{agent_id}] tool={block.name} → {str(result)[:80]}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
            messages.append({"role": "user", "content": tool_results})
        else:
            break

    print(f"  ✓ [{agent_id}] complete ({turn + 1} turns)")
    return {"agent_id": agent_id, "result": final_text}

# ─── Agent output file map (used for skip-existing logic) ─────────────────────

AGENT_OUTPUTS = {
    "hardware_scout":   ["hardware_recommendation.md"],
    "cv_engineer":      ["cv_pipeline.py"],
    "gpu_optimizer":    ["gpu_accelerated.py"],
    "protocol_designer":["serial_protocol.py", "PROTOCOL_SPEC.md"],
    "esp32_firmware":   ["esp32_main.cpp", "platformio.ini"],
    "system_architect": ["main.py", "config.yaml"],
    "docs_writer":      ["SETUP.md"],
}

def agent_is_done(agent_id: str) -> bool:
    """Returns True if all expected output files for this agent already exist."""
    files = AGENT_OUTPUTS.get(agent_id, [])
    return bool(files) and all((OUTPUT_DIR / f).exists() for f in files)

# ─── Agent definitions ────────────────────────────────────────────────────────

def make_agents(notebook_context: str = "") -> list[dict]:
    """
    Returns the 7 agent task definitions.
    notebook_context: optional paste of NotebookLM content to inject.
    """
    extra = f"\n\nAdditional context from project notebooks:\n{notebook_context}" if notebook_context else ""

    return [
        # ── 1. Hardware & Platform Scout ──────────────────────────────────────
        {
            "agent_id": "hardware_scout",
            "model": MODEL_ARCHITECT,
            "system_prompt": textwrap.dedent(f"""
                ## Skill Self-Assignment
                You are activating the following specialist skills for this task:
                - **Plan**: Strategic technical evaluation and recommendation
                - **Software Architect**: Platform and stack decisions
                - **Embedded Firmware Engineer**: ESP32 and Jetson constraints

                At the start of your response, state your active skills.

                {PROJECT_CONTEXT}{extra}
            """).strip(),
            "task_prompt": textwrap.dedent("""
                Evaluate and recommend the optimal hardware/software platform for the ME135 project.
                Cover all of the following, then write your findings to `hardware_recommendation.md`:

                1. **PS3 Eye Camera drivers**
                   - gspca_ov534 (kernel module) setup on Ubuntu 22.04 / Jetson L4T
                   - v4l2 device path (/dev/video0), resolution, framerate options
                   - OpenCV VideoCapture initialization snippet

                2. **Processing platform trade-off**
                   - Jetson Nano (128 CUDA cores) vs Jetson Orin Nano (1024 CUDA cores) vs bare ESP32
                   - Output is 64×64 (panel native). Internal CV res can be higher (e.g., 320×240 or 640×480)
                     then resized down before transmission. Which platform handles BG sub + resize at 30 fps?
                   - Memory footprint estimate for the 64×64 binary matrix pipeline (trivially small —
                     payload is 512 bytes/frame; bottleneck is CV, not transport)

                3. **Python vs LabVIEW** (IMPORTANT: LabVIEW Student Edition is the course-required tool)
                   - LabVIEW: NI Vision Dev Module for cameras, Producer/Consumer DAQ pattern,
                     hardware-timed loops, AI Toolkit + OpenCV integration
                   - Python: More flexible for GPU (CuPy, PyTorch), better community support
                   - Recommend primary LabVIEW implementation + Python GPU variant as bonus

                4. **Library stack recommendation**
                   - OpenCV 4.x with CUDA, CuPy, NumPy, PySerial
                   - Any Jetson-specific packages (jetson-utils, etc.)

                Save a clean Markdown document to `hardware_recommendation.md`.
            """).strip(),
        },

        # ── 2. Computer Vision Pipeline Engineer ──────────────────────────────
        {
            "agent_id": "cv_engineer",
            "model": MODEL_CODER,
            "system_prompt": textwrap.dedent(f"""
                ## Skill Self-Assignment
                You are activating the following specialist skills:
                - **AI Engineer**: Machine learning pipeline design
                - **Computer Vision Pipeline Specialist**: OpenCV, background subtraction, thresholding

                At the start of your response, state your active skills.

                {PROJECT_CONTEXT}{extra}
            """).strip(),
            "task_prompt": textwrap.dedent("""
                Write a complete, production-quality Python CV pipeline (`cv_pipeline.py`) that:

                1. Opens the PS3 Eye Camera via OpenCV VideoCapture (or reads a video file for testing)
                2. **Calibration phase**: reads `calibration.mp4` (or first N frames of the live feed)
                   and builds a background model using `cv2.createBackgroundSubtractorMOG2`
                   - Justify MOG2 vs KNN vs simple frame-differencing for this use case
                3. **Processing loop**:
                   - Capture frame → apply background subtractor → get foreground mask
                   - Morphological cleanup (dilate/erode) to remove noise
                   - Resize mask to 64×64 (`cv2.resize` with INTER_AREA, then threshold) —
                     this matches the Waveshare RGB-Matrix-P2 panel native resolution
                   - Threshold to pure 0/1 (`np.where(mask > 0, 1, 0).astype(np.uint8)`)
                   - Return the 64×64 ndarray
                4. Module interface:
                   ```python
                   class CameraProcessor:
                       def __init__(self, source, calibration_video=None): ...
                       def calibrate(self, n_frames=100): ...
                       def get_binary_frame(self) -> np.ndarray: ...  # shape (64, 64), dtype uint8
                       def release(self): ...
                   ```
                5. Include a `__main__` block that shows a live preview and prints FPS.

                Write the file to `cv_pipeline.py`.
            """).strip(),
        },

        # ── 3. GPU Acceleration Optimizer ─────────────────────────────────────
        {
            "agent_id": "gpu_optimizer",
            "model": MODEL_CODER,
            "system_prompt": textwrap.dedent(f"""
                ## Skill Self-Assignment
                You are activating the following specialist skills:
                - **AI Engineer**: GPU-accelerated ML/CV pipelines
                - **CUDA/Jetson Specialist**: OpenCV CUDA module, CuPy, NVIDIA Jetson optimization

                At the start of your response, state your active skills.

                {PROJECT_CONTEXT}{extra}
            """).strip(),
            "task_prompt": textwrap.dedent("""
                Write `gpu_accelerated.py` — a GPU-accelerated drop-in replacement for the
                background subtraction and resize pipeline on NVIDIA Jetson.

                Requirements:
                1. **OpenCV CUDA backend**:
                   - `cv2.cuda.GpuMat` for frame upload/download
                   - `cv2.cuda.createBackgroundSubtractorMOG2()` for GPU background subtraction
                   - `cv2.cuda.resize()` to 64×64 (panel native, Waveshare RGB-Matrix-P2)
                   - `cv2.cuda.threshold()` for binarization

                2. **Graceful CPU fallback**:
                   ```python
                   try:
                       USE_GPU = cv2.cuda.getCudaEnabledDeviceCount() > 0
                   except:
                       USE_GPU = False
                   ```

                3. **Same interface as CameraProcessor** in cv_pipeline.py
                   (import and swap transparently)

                4. **Benchmark utility**:
                   ```python
                   def benchmark(n_frames=200): ...  # prints CPU ms vs GPU ms per frame
                   ```

                5. Memory optimization notes:
                   - Pin host memory for fast DMA transfers
                   - Avoid repeated GpuMat allocations in the loop

                Write to `gpu_accelerated.py`.
            """).strip(),
        },

        # ── 4. Serial Protocol Designer ───────────────────────────────────────
        {
            "agent_id": "protocol_designer",
            "model": MODEL_CODER,
            "system_prompt": textwrap.dedent(f"""
                ## Skill Self-Assignment
                You are activating the following specialist skills:
                - **Backend Architect**: Data serialization, protocol design
                - **Embedded Firmware Engineer**: Serial communication constraints, UART buffers

                At the start of your response, state your active skills.

                {PROJECT_CONTEXT}{extra}
            """).strip(),
            "task_prompt": textwrap.dedent("""
                Design and implement the serial communication protocol for transmitting the
                64×64 binary matrix from the host (Jetson/PC) to the ESP32.

                **Math baseline**: 64×64 = 4,096 bits → bit-packed = 512 bytes/frame

                Write `serial_protocol.py` (Python host side) containing:

                1. **Frame format**:
                   ```
                   [SYNC 0xAA 0x55] [2-byte frame_id] [512 bytes bit-packed data] [2-byte CRC16]
                   = 518 bytes total per frame
                   ```

                2. **Bit packing**: 8 pixels → 1 byte (MSB first, row-major order)
                   ```python
                   def pack_matrix(matrix: np.ndarray) -> bytes:
                       # Pack 64x64 binary ndarray into 512 bytes.
                   ```

                3. **MatrixSender class**:
                   ```python
                   class MatrixSender:
                       def __init__(self, port: str, baud: int = 921600): ...
                       def send_frame(self, matrix: np.ndarray) -> bool: ...
                       def close(self): ...
                   ```

                4. **Baud rate analysis** (include as a docstring or comment):
                   - At 115200 bps: 518 bytes = ~45 ms/frame → 22 fps
                   - At 921600 bps: ~5.6 ms/frame → 178 fps theoretical (CV will be the bottleneck)
                   - At 3Mbps (USB CDC / FTDI): ~1.7 ms/frame
                   - Recommendation: 921600 bps is plenty for 60+ fps; 3Mbps if you want slack

                5. RLE compression is unnecessary at 64×64 — 512 bytes/frame is already trivial.

                Also write `PROTOCOL_SPEC.md` documenting the frame format.

                Write both files.
            """).strip(),
        },

        # ── 5. ESP32 Firmware Engineer ────────────────────────────────────────
        {
            "agent_id": "esp32_firmware",
            "model": MODEL_CODER,
            "system_prompt": textwrap.dedent(f"""
                ## Skill Self-Assignment
                You are activating the following specialist skills:
                - **Embedded Firmware Engineer**: ESP32 Arduino/PlatformIO, FreeRTOS tasks
                - **Hardware Interface Specialist**: HUB75 RGB matrix panels, ESP32-HUB75-MatrixPanel-DMA

                At the start of your response, state your active skills.

                {PROJECT_CONTEXT}{extra}
            """).strip(),
            "task_prompt": textwrap.dedent("""
                Write complete ESP32 firmware that receives the 64×64 binary matrix
                over serial and drives the Waveshare RGB-Matrix-P2 64×64 panel via HUB75.
                Reference: https://www.waveshare.com/wiki/RGB-Matrix-P2-64x64

                Produce two files:

                **File 1: `esp32_main.cpp`** (Arduino/PlatformIO)

                Core logic:
                1. **Serial receiver**:
                   - Listen on Serial (USB CDC, 921600 or 3Mbps) for frame packets
                   - Parse: SYNC [0xAA,0x55] | 2-byte frame_id | 512 bytes | 2-byte CRC16
                   - Validate CRC; discard bad frames
                   - Double-buffer: fill `backBuffer` while `frontBuffer` is being displayed

                2. **HUB75 panel output**:
                   - Use the `ESP32-HUB75-MatrixPanel-DMA` library (Mrfaptastic)
                   - Configure `HUB75_I2S_CFG` with PANEL_RES_X=64, PANEL_RES_Y=64, PANEL_CHAIN=1
                   - Default HUB75 pin map for ESP32 (R1,G1,B1,R2,G2,B2,A,B,C,D,E,LAT,OE,CLK)
                   - Map binary matrix bit → `dma_display->drawPixel(x, y, bit ? 0xFFFF : 0x0000)`
                   - Or batch via `fillScreenRGB888` for speed
                   - DO NOT use FastLED / NeoPixel paths — wrong interface for this panel

                3. **FreeRTOS tasks**:
                   - `TaskRx`: serial receive + CRC check (Core 0)
                   - `TaskDisplay`: swap buffers + push to HUB75 DMA (Core 1)

                4. Include a watchdog reset if no frame received in 5 seconds.

                **File 2: `platformio.ini`**
                - Board: `esp32dev` (or `esp32-s3-devkitc-1`)
                - Framework: arduino
                - Dependencies: `mrfaptastic/ESP32 HUB75 LED MATRIX PANEL DMA Display`
                - monitor_speed: 921600

                Write both files.
            """).strip(),
        },

        # ── 6. System Integration Architect ───────────────────────────────────
        {
            "agent_id": "system_architect",
            "model": MODEL_ARCHITECT,
            "system_prompt": textwrap.dedent(f"""
                ## Skill Self-Assignment
                You are activating the following specialist skills:
                - **Software Architect**: Multi-threaded pipeline design, module composition
                - **Backend Architect**: Configuration management, inter-process communication
                - **DevOps Automator**: requirements.txt, launch scripts

                At the start of your response, state your active skills.

                {PROJECT_CONTEXT}{extra}
            """).strip(),
            "task_prompt": textwrap.dedent("""
                Read the existing agent outputs to understand what has been built, then write
                the integration layer that ties everything together.

                First, read these files if they exist: cv_pipeline.py, gpu_accelerated.py, serial_protocol.py

                Then produce:

                **File 1: `main.py`** — The main entry point
                ```python
                # Threaded pipeline:
                # Thread 1: capture + CV processing (CameraProcessor or GPU variant)
                # Thread 2: serial sender (MatrixSender)
                # Main thread: FPS counter + keyboard interrupt handler
                ```

                Structure:
                - `argparse` for: --camera (device id), --calibration (video path),
                  --port (serial port), --baud, --gpu (flag), --display (preview window flag)
                - Calibration sequence on startup (with progress bar via tqdm)
                - Producer/consumer queue between capture thread and sender thread
                - Graceful shutdown on Ctrl+C (release camera, close serial)
                - FPS stats printed every 5 seconds

                **File 2: `config.yaml`**
                All tunable parameters: camera_id, resolution, target_fps, calibration_frames,
                bg_subtractor_params, output_width, output_height, serial_port, baud_rate, gpu_enabled

                **File 3: `requirements.txt`**
                opencv-python-headless, numpy, pyserial, tqdm, pyyaml, cupy-cuda12x (optional)

                Read existing files first, then write all three.
            """).strip(),
        },

        # ── 7. Setup & Docs Writer ────────────────────────────────────────────
        {
            "agent_id": "docs_writer",
            "model": MODEL_CODER,
            "system_prompt": textwrap.dedent(f"""
                ## Skill Self-Assignment
                You are activating the following specialist skills:
                - **Technical Writer**: Clear, step-by-step technical documentation
                - **Embedded Firmware Engineer**: Driver installation, hardware wiring

                At the start of your response, state your active skills.

                {PROJECT_CONTEXT}{extra}
            """).strip(),
            "task_prompt": textwrap.dedent("""
                Write a comprehensive hardware and software setup guide for the ME135 team.

                Write `SETUP.md` covering:

                ## 1. Bill of Materials
                - PS3 Eye Camera (~$8 on eBay)
                - NVIDIA Jetson Nano 4GB (or Orin Nano 8GB)
                - ESP32-S3 DevKit (for faster USB CDC)
                - Waveshare RGB-Matrix-P2 64×64 LED panel (HUB75, 4096 RGB LEDs)
                  https://www.waveshare.com/wiki/RGB-Matrix-P2-64x64
                - 5V / ≥4A power supply for the panel (≈ 4 A peak at full white,
                  much less typical for sparse silhouette rendering)
                - HUB75 ribbon cable + 16-pin IDC connector (panel-to-ESP32 wiring)
                - USB-A to Micro-USB cable (PS3 cam) + USB-C (Jetson + ESP32)

                ## 2. PS3 Eye Camera Driver Install (Ubuntu 20.04/22.04 + Jetson L4T)
                ```bash
                # gspca_ov534 is included in kernel — verify:
                sudo modprobe gspca_ov534
                v4l2-ctl --list-devices
                # Test capture:
                ffmpeg -f v4l2 -i /dev/video0 -vframes 1 test.jpg
                ```

                ## 3. Jetson CUDA + OpenCV Setup
                - JetPack SDK installation (which bundles CUDA + cuDNN + OpenCV with CUDA)
                - Verify: `python3 -c "import cv2; print(cv2.cuda.getCudaEnabledDeviceCount())"`

                ## 4. Python Environment
                ```bash
                python3 -m venv venv && source venv/bin/activate
                pip install -r requirements.txt
                ```

                ## 5. ESP32 Wiring
                - Serial connection: Jetson TX → ESP32 RX, GND → GND
                - Level shifting: Jetson GPIO is 3.3V; ESP32 is also 3.3V → direct connect OK
                - LED strip power: separate 5V supply; data line: ESP32 GPIO pin (e.g. GPIO 18)

                ## 6. Running the Pipeline
                ```bash
                # Step 1: Record calibration video (5 seconds, empty room)
                python3 main.py --calibrate-only --duration 5 --output calibration.mp4

                # Step 2: Run full pipeline
                python3 main.py --camera 0 --calibration calibration.mp4 --port /dev/ttyUSB0 --gpu
                ```

                ## 7. Troubleshooting
                - Camera not found: check `ls /dev/video*` and driver load
                - Serial permission: `sudo usermod -a -G dialout $USER` then re-login
                - ESP32 not receiving: check baud rate matches both ends, CRC errors in ESP32 Serial monitor
                - Low FPS: try `--no-gpu` to compare, or reduce resolution first

                Write to `SETUP.md`.
            """).strip(),
        },
    ]

# ─── Integration agent (runs after all 7) ────────────────────────────────────

async def run_integration_agent(client: anthropic.AsyncAnthropic, results: list[dict]) -> None:
    """
    Reads all generated files and produces a PROJECT_README.md that ties
    everything together with a dependency map and ordered setup steps.
    """
    print("\n  ▶ [integration] synthesizing all agent outputs...")

    summary_lines = "\n".join(
        f"- {r['agent_id']}: {'COMPLETED' if r['result'] else 'NO OUTPUT'}"
        for r in results
    )

    task = textwrap.dedent(f"""
        All specialized agents have finished. Here's a status summary:
        {summary_lines}

        Please:
        1. Read each output file in agent_outputs/ (use read_file for each)
        2. Identify any import inconsistencies or naming mismatches between files
        3. Write `PROJECT_README.md` containing:
           - Project overview and architecture diagram (ASCII)
           - File dependency tree
           - Ordered quick-start steps (5 steps or fewer)
           - Key configuration parameters table
           - Any known issues or TODOs flagged across agent outputs

        Files to read and synthesize:
        hardware_recommendation.md, cv_pipeline.py, gpu_accelerated.py,
        serial_protocol.py, PROTOCOL_SPEC.md, esp32_main.cpp, platformio.ini,
        main.py, config.yaml, requirements.txt, SETUP.md
    """).strip()

    system = textwrap.dedent(f"""
        ## Skill Self-Assignment
        You are activating: Software Architect, Technical Writer, Code Reviewer.

        You are the integration architect for the ME135 project. Your job is to
        synthesize all specialist agent outputs into a coherent whole.

        {PROJECT_CONTEXT}
    """).strip()

    await run_agent(
        client,
        agent_id="integration",
        system_prompt=system,
        task_prompt=task,
        model=MODEL_ARCHITECT,
        max_turns=20,
    )
    print("  ✓ [integration] PROJECT_README.md written")

# ─── Main ─────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="ME135 Claude Agent Swarm")
    parser.add_argument(
        "--notebooks",
        type=str,
        default="",
        help="Paste NotebookLM content here to inject into all agents"
    )
    parser.add_argument(
        "--agents",
        nargs="*",
        help="Run only specific agents by ID (default: all). "
             "IDs: hardware_scout cv_engineer gpu_optimizer protocol_designer "
             "esp32_firmware system_architect docs_writer"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-run all agents even if their output files already exist."
    )
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("Error: ANTHROPIC_API_KEY environment variable not set.")

    client = anthropic.AsyncAnthropic(api_key=api_key)
    agents = make_agents(notebook_context=args.notebooks)

    # Filter agents if --agents flag used
    if args.agents:
        agents = [a for a in agents if a["agent_id"] in args.agents]
        if not agents:
            raise SystemExit(f"No matching agents found. Available: {[a['agent_id'] for a in make_agents()]}")

    # Skip agents whose output files already exist (unless --force)
    if not args.force:
        skipped = [a for a in agents if agent_is_done(a["agent_id"])]
        agents   = [a for a in agents if not agent_is_done(a["agent_id"])]
        if skipped:
            print(f"\n  Skipping {len(skipped)} already-complete agent(s) "
                  f"(use --force to re-run):")
            for a in skipped:
                files = ", ".join(AGENT_OUTPUTS[a["agent_id"]])
                print(f"    ✓ [{a['agent_id']}] → {files}")
        if not agents:
            print("\n  All agents already done. Run with --force to regenerate.")
            return

    print(f"\n{'='*60}")
    print(f"  ME135 Camera Processing — Agent Swarm")
    print(f"  Launching {len(agents)} agents in parallel")
    print(f"  Output directory: {OUTPUT_DIR}")
    print(f"{'='*60}\n")

    # ── Phase 1: All specialist agents run in parallel ──────────────────────
    tasks = [
        run_agent(
            client,
            agent_id=a["agent_id"],
            system_prompt=a["system_prompt"],
            task_prompt=a["task_prompt"],
            model=a.get("model", MODEL_CODER),
        )
        for a in agents
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Normalize exceptions into result dicts
    clean_results = []
    for r, a in zip(results, agents):
        if isinstance(r, Exception):
            print(f"  ✗ [{a['agent_id']}] FAILED: {r}")
            clean_results.append({"agent_id": a["agent_id"], "result": ""})
        else:
            clean_results.append(r)

    # ── Phase 2: Integration agent synthesizes everything ──────────────────
    await run_integration_agent(client, clean_results)

    print(f"\n{'='*60}")
    print(f"  Swarm complete! {len(clean_results)} agents ran.")
    print(f"  All files in: {OUTPUT_DIR}/")
    print(f"{'='*60}\n")

    # Print manifest
    files = sorted(OUTPUT_DIR.iterdir())
    for f in files:
        size = f.stat().st_size
        print(f"  {f.name:<35} {size:>8,} bytes")

if __name__ == "__main__":
    asyncio.run(main())
