# ME135 | Vision Pipeline

> Evolution log — one section per commit.

---

## v1 — 2026-05-09 12:39 — `c898c6b`

### What Changed

This batch of commits transforms a single-mode silhouette display into a **dual-mode system** (mask + fingertip tracking) and fixes a hardware wiring error that could have bricked setups built from the old docs.

### ESP32 Firmware (`main.cpp`) — Major protocol & rendering overhaul

- **Multi-mode serial protocol**: The frame format gained a `MODE` byte between the length field and payload. The old wire format was `[AA 55][LEN][512B payload][CRC][55 AA]`; the new one is `[AA 55][LEN][MODE][payload...][CRC][55 AA]`. CRC now covers MODE + payload (previously payload-only). This is a **breaking wire-protocol change** — both sides must be updated together.
- **MODE_FINGERTIPS (0x01)**: New render path. Receives `[count][x,y,r,g,b]×N` packets (up to 10 fingertips) and draws each as a 3×3 colored block on the panel. Purpose: a second team member (Wen) can drive the panel with hand-tracked colored dots instead of a silhouette mask.
- **Button-driven mode toggle (GPIO 33)**: A physical button on the ESP32 switches between mask mode and fingertip mode at runtime, with proper debounce (50 ms). The ESP32 sends `0x10`/`0x11` notification bytes upstream so the Mac knows which packet type to send.
- **Render refactor**: The old monolithic `renderFrame(r,g,b)` split into `renderMask()` (pot-driven white→red lerp baked in) and `renderFingertips()`. The panel is now blanked before each redraw — important because fingertip mode only draws sparse dots.
- **Watchdog scoped to mode 0 only**: The 5-second "blank if Mac stops sending" watchdog now only fires in mask mode. Fingertip mode stays on last frame — reasonable since finger tracking is inherently sporadic.
- **Cleanup**: Removed `COLOR_DEPTH` define and commented-out `setPixelColorDepthBits` call. Stripped verbose inline comments that were more tutorial than maintenance documentation.

### Serial Protocol (`serial_protocol.py`) — Mode-aware framing

- **`build_frame(mode, payload)`**: New universal frame builder that inserts the mode byte and computes CRC over `mode + payload`. Replaces the old mask-only framer.
- **`pack_fingertips()` / `unpack_fingertips()`**: Encode/decode fingertip lists into the compact `[count][x,y,r,g,b]...` binary format. Symmetric with `pack_mask()`/`unpack_mask()`.
- **`SerialSender` now mode-aware**: Exposes `send_mask()` and `send_fingertips()` as separate entry points. Tracks `esp32_mode` internally and provides `read_mode_change()` to poll for button-press notifications from the ESP32.
- **Mode notification constants**: `MODE_NOTIFY_0` (0x10) and `MODE_NOTIFY_1` (0x11) defined. These single-byte upstream messages let the ESP32 tell the Mac "I switched modes."

### Vision Pipeline (`vision_send.py`) — Unified YOLO + MediaPipe

- **Dual pipeline**: Every frame now runs *both* YOLO person segmentation (silhouette mask) and MediaPipe hand tracking (fingertip positions). Which result gets sent depends on the ESP32's current mode.
- **MediaPipe integration**: `extract_fingertips()` reads 5 landmark IDs (thumb tip, index tip, etc.) per detected hand (up to 2 hands), maps them to 64×64 panel coordinates, and assigns cycling RGB colors.
- **Mode-driven TX**: The main loop checks `sender.esp32_mode` and calls either `send_mask()` or `send_fingertips()`. Mode changes are received passively via `read_mode_change()`.
- **Preview enhancements**: Camera overlay now shows both fingertip dots and silhouette contours simultaneously, regardless of which mode is active on the ESP32. The 64×64 preview grid shows fingertip positions too.

### Hardware Docs (`WIRING.md`) — Critical pin-numbering fix

- **HUB75 pin table completely reordered**: The old table used Adafruit-style numbering (R1 at pin 1, GND at pin 16). The Waveshare RGB-Matrix-P2 64×64 numbers pins in *reverse* — pin 1 is GND/OE at the bottom-right, pin 16 is R1 at the top-left. **Anyone wiring from the old table would have had every signal on the wrong pin.** The GPIO assignments themselves didn't change — only which physical IDC pin each signal lands on.
- **Pin map comment in `main.cpp`** updated to show Waveshare pin numbers alongside GPIOs: e.g., `R1(16)=25` meaning "Waveshare HUB75 pin 16 → GPIO 25."

### Housekeeping

- **`fa3fc0f`**: Archived stale `agent_outputs/` directory and fixed a doc-generation hook that was creating infinite auto-commit loops.
- **`32d47a7`**: Fixed pot description in docs — it triggers effects (white→red lerp), not brightness control.

### Evolution Timeline

```mermaid
gitGraph
    commit id: "9087b6c" tag: "v0.2" type: HIGHLIGHT
    commit id: "fa3fc0f"
    commit id: "32d47a7"
    commit id: "c898c6b" tag: "HEAD"
```

| Commit | Date | Summary | Subsystems touched |
|--------|------|---------|--------------------|
| `9087b6c` | May 7 | **64×64 mask → ESP32 → HUB75 panel + pot lerp** — Initial working pipeline: YOLO silhouette, serial framing, HUB75 DMA rendering, pot-controlled color | CV pipeline, serial protocol, ESP32 firmware |
| `fa3fc0f` | May 8 | Archive stale `agent_outputs/`, fix doc-hook auto-commit loop | Repo hygiene, CI |
| `32d47a7` | May 8 | Fix pot description — triggers white→red effect, not brightness | Docs |
| `c898c6b` | May 9 | **Waveshare pin-numbering fix** + finger-glove mode (MODE_FINGERTIPS), button debounce, MediaPipe integration, mode-aware protocol | ESP32 firmware, serial protocol, CV pipeline, hardware docs |

### Subsystem evolution across commits

```mermaid
timeline
    title Subsystem Activity per Commit
    section 9087b6c — Foundation
        CV Pipeline       : YOLO segmentation, 64x64 downscale, serial TX
        Serial Protocol   : Binary mask framing, CRC16, ACK/NAK
        ESP32 Firmware    : HUB75 DMA init, RX state machine, pot EWMA, render loop
    section fa3fc0f — Cleanup
        Repo Hygiene      : Archive stale outputs, fix CI loop
    section 32d47a7 — Docs
        Documentation     : Correct pot behavior description
    section c898c6b — Dual Mode
        CV Pipeline       : Add MediaPipe hand tracking, dual pipeline
        Serial Protocol   : Mode byte in frame, fingertip pack/unpack, mode notifications
        ESP32 Firmware    : MODE_FINGERTIPS, GPIO33 button, renderFingertips(), scoped watchdog
        Hardware Docs     : Reorder HUB75 pin table to match Waveshare silkscreen
```

**Key architectural milestone at `c898c6b`:** The system evolves from a single-purpose silhouette display into a mode-switchable platform. The ESP32 is now the mode authority (physical button), and the Mac adapts its TX format based on upstream notifications. This inverts the original control flow where the Mac was the sole driver.

### System Architecture

```mermaid
flowchart TD
  subgraph CAM["🎥 PS3 Eye Camera"]
    PS3["Sony PS3 Eye\nUSB · 640×480 · 60 fps\nDriver: gspca_ov534"]
  end

  subgraph HOST["🖥 Jetson / Mac — vision/vision_send.py  [CPU + GPU]"]
    direction TB

    CAP["cv2.VideoCapture\nopen_camera() — tries AVFoundation → V4L2 → ANY\n640×480 BGR · ~921,600 B raw/frame"]

    subgraph GPU_BLOCK["GPU — YOLOv8n-seg Inference"]
      YOLO["ultralytics YOLO\nmodel.predict(classes=[0], imgsz=640)\nyolov8n-seg.pt  ~6 MB checkpoint\noutput: masks (N,480,640) + boxes (N,4)"]
    end

    subgraph CPU_BLOCK["CPU — Silhouette & Hand Tracking"]
      MP["mediapipe.solutions.hands\nmax_num_hands=2 · conf=0.6\n21 landmarks/hand → 5 fingertips"]
      MASK["Silhouette Build\nnp.maximum OR all person masks\nmorphologyEx MORPH_CLOSE 5×5\nfindContours · drop < 0.2% area"]
      SMALL["cv2.resize → 64×64\nINTER_AREA downscale\n4,096 B binary uint8"]
    end

    subgraph PROTO["serial_protocol.py — Frame Assembly"]
      PACK_M["pack_mask()\nnp.packbits MSB-first row-major\n512 B bit-packed payload"]
      PACK_F["pack_fingertips()\n[count(1)] + [x y r g b](5) × N\nmax 10 tips → max 51 B payload"]
      BFRAME["build_frame(mode, payload)\n[AA 55][LEN_H LEN_L][MODE][payload][CRC16][55 AA]\nMode 0: 521 B total · Mode 1: 12–62 B"]
      CRC_TX["crc16_ccitt()\npoly=0x1021 init=0xFFFF\ncovers MODE byte + payload"]
    end

    SENDER["SerialSender\nserial.Serial @ 1,000,000 baud\nACK timeout=50 ms · max_retries=3\nTX gate: ≥ 33 ms between frames"]
  end

  subgraph ESP32["⚡ ESP32 DevKitC — firmware/me135_led_pot/src/main.cpp"]
    direction TB

    RXM["pollFrame() State Machine\nRxBuffer = 2,048 B\nRX_WAIT_AA → 55 → LEN → MODE\n→ PAYLOAD → CRC → END\nFrame timeout = 100 ms"]
    CRC_RX["crc16_ccitt() verify\nRX_OK → ACK 0x06\nRX_CRC_ERROR → NAK 0x15\nWatchdog blank after 5,000 ms"]

    FB["framebuf[512]\nlast-good mask cache\nrxbuf[512] in-flight buffer"]

    subgraph INPUTS["Analog & Digital Inputs"]
      POT["GPIO 34 — ADC1_CH6\n10 kΩ pot · 12-bit (0–4095)\nEWMA filter → t ∈ [0,1]\nwhite (t=0) → red (t=1)"]
      BTN["GPIO 33 — INPUT_PULLUP\nMode toggle button\nDebounce 50 ms\nSends MODE_NOTIFY 0x10/0x11"]
    end

    subgraph RENDER_BLOCK["Render Engine"]
      REND_M["renderMask()\n4,096 px loop · unpack bits\ndrawPixelRGB888(x,y, 255,\n  (1-t)×255, (1-t)×255)"]
      REND_F["renderFingertips()\n3×3 block per tip\ndrawPixelRGB888(x,y, r,g,b)"]
      BLANK["blankPanel()\nfillScreenRGB888(0,0,0)\non watchdog / no data"]
    end

    DMA["ESP32-HUB75-MatrixPanel-DMA v3.0.11\nI2S DMA double-buffer\n1/32 scan HUB75E timing"]
  end

  subgraph PSU["🔌 Power"]
    P5V["5V / 3A+ DC PSU\nPanel VH4 connector\n1,000–2,000 µF bulk cap"]
    PUSB["Mac USB 5V/500mA\nESP32 logic only"]
    GND["Common Ground\nESP32 GND ↔ PSU GND\n⚠️ mandatory"]
  end

  subgraph PANEL["💡 Waveshare RGB-Matrix-P2 64×64"]
    HUB75["HUB75E 16-pin IDC\nR1 G1 B1 R2 G2 B2  → GPIOs 25 26 27 14 12 13\nA B C D E          → GPIOs 23 19 5 17 32\nLAT OE CLK         → GPIOs 4 15 16\n4,096 RGB LEDs · 2 mm pitch · 128×128 mm"]
  end

  PS3            -->|"USB · BGR 640×480\n~921,600 B/frame"| CAP
  CAP            --> YOLO
  CAP            --> MP
  YOLO           -->|"masks + boxes\n(N,480,640) float32"| MASK
  MP             -->|"5 tip (x,y) per hand\nnormalized [0,1]"| SMALL
  MASK           --> SMALL
  SMALL          -->|"64×64 binary\n4,096 B"| PACK_M
  SMALL          -->|"mode 1 path"| PACK_F
  PACK_M         -->|"512 B"| BFRAME
  PACK_F         -->|"1–51 B"| BFRAME
  BFRAME         --> CRC_TX
  CRC_TX         --> SENDER
  SENDER         -->|"USB-CDC · 1,000,000 baud\nMode 0: 521 B/frame ≈ 4.2 ms TX\nMode 1: ≤ 62 B/frame"| RXM
  RXM            --> CRC_RX
  CRC_RX         -->|"RX_OK\nmemcpy rxbuf→framebuf"| FB
  CRC_RX         -->|"NAK 0x15\n1 byte back"| SENDER
  CRC_RX         -->|"ACK 0x06\n1 byte back"| SENDER
  FB             --> REND_M
  FB             --> REND_F
  POT            -->|"12-bit ADC\nEWMA t"| REND_M
  BTN            -->|"0x10 / 0x11\nmode notify"| SENDER
  REND_M         --> DMA
  REND_F         --> DMA
  BLANK          --> DMA
  DMA            -->|"HUB75E 16-pin ribbon\n< 30 cm recommended"| HUB75
  P5V            -->|"5V / 3A · VH4"| HUB75
  PUSB           -->|"5V / 500 mA"| ESP32
  GND            --- ESP32
  GND            --- P5V
```

| Layer | Key Numbers |
|---|---|
| Camera raw frame | 640 × 480 × 3 B = **921,600 B** |
| YOLO inference size | 640 px letterboxed (GPU) |
| Silhouette at panel res | 64 × 64 = **4,096 B** (binary) |
| Bit-packed payload (Mode 0) | **512 B** (64 × 64 / 8) |
| Full framed packet (Mode 0) | **521 B** = 2+2+1+512+2+2 |
| Baud rate | **1,000,000 baud** USB-CDC |
| TX time per frame | ≈ **4.2 ms** → headroom for 30 fps |
| ESP32 RX buffer | **2,048 B** |
| Watchdog blank timeout | **5,000 ms** no-data |
| Panel power | **5 V / 3 A+** (separate PSU) |

### Data Flow

One frame's journey — camera shutter to glowing LED panel.

```mermaid
sequenceDiagram
  autonumber
  participant CAM  as 🎥 PS3 Eye Camera
  participant CPU  as 🖥 Jetson CPU
  participant GPU  as ⚡ Jetson GPU<br/>(YOLOv8n-seg)
  participant MP   as 🖐 MediaPipe<br/>(CPU)
  participant SP   as 📦 serial_protocol.py
  participant SER  as 🔌 USB-CDC Serial<br/>1,000,000 baud
  participant RX   as 📡 ESP32 RX<br/>State Machine
  participant REND as 🎨 ESP32 Renderer
  participant DMA  as 🚌 HUB75-DMA<br/>I2S Driver
  participant PNL  as 💡 64×64<br/>LED Panel

  Note over CAM,CPU: ── t = 0 ms ── Frame Capture ──────────────────────────────
  CAM  ->> CPU:  cap.read() → BGR frame<br/>640 × 480 × 3 B = 921,600 B

  Note over CPU,GPU: ── t ≈ 1 ms ── Dual Inference (parallel) ─────────────────
  CPU  ->> GPU:  model.predict(frame, classes=[0], imgsz=640, verbose=False)
  CPU  ->> MP:   hands.process(cv2.cvtColor(frame, BGR→RGB))

  GPU  -->> CPU: masks (N, 480, 640) float32  +  boxes (N, 4) int<br/>~10–40 ms on Jetson GPU
  MP   -->> CPU: multi_hand_landmarks · 21 × (x,y,z) per hand<br/>~5 ms on CPU

  Note over CPU: ── t ≈ 20 ms ── Silhouette Build ────────────────────────────
  CPU  ->> CPU:  .cpu().numpy() → host RAM (GPU→CPU copy, ~N×480×640 B)
  CPU  ->> CPU:  np.maximum() OR all person masks → silhouette 640×480
  CPU  ->> CPU:  morphologyEx(MORPH_CLOSE, 5×5 kernel, iter=1)
  CPU  ->> CPU:  findContours → filter area ≥ 0.2% of frame (~614 px²)
  CPU  ->> CPU:  drawContours(clean, filled) → uint8 640×480 = 307,200 B

  Note over CPU: ── t ≈ 22 ms ── Downscale to Panel Resolution ───────────────
  CPU  ->> CPU:  cv2.resize(clean, (64,64), INTER_AREA)<br/>307,200 B → 4,096 B
  CPU  ->> CPU:  cv2.threshold(> 96) → binary {0, 255}

  Note over CPU,SP: ── t ≈ 23 ms ── Pack + Frame Assembly ────────────────────
  CPU  ->> SP:   send_mask(small_64x64)
  SP   ->> SP:   pack_mask(): np.ascontiguousarray → flatten 4096 elements<br/>np.packbits(bitorder='big') → 512 B  [64×64÷8]
  SP   ->> SP:   build_frame(MODE=0x00, payload=512 B)
  Note right of SP: Frame layout:<br/>[AA 55] — SOF 2 B<br/>[02 00] — LEN  2 B  (=512)<br/>[00]    — MODE 1 B<br/>[512 B] — payload<br/>[CRC_H CRC_L] — 2 B<br/>[55 AA] — EOF 2 B<br/>────────────────<br/>Total: 521 B
  SP   ->> SP:   crc16_ccitt([MODE] + payload[512]) → uint16<br/>poly=0x1021, init=0xFFFF, no reflect

  Note over SP,SER: ── t ≈ 24 ms ── Transmit ──────────────────────────────────
  SP   ->> SER:  serial.reset_input_buffer()
  SP   ->> SER:  serial.write(521 B) + flush()<br/>521 B × 8 bits ÷ 1,000,000 baud = 4.168 ms TX time

  Note over SER,RX: ── t ≈ 28 ms ── ESP32 Receive ──────────────────────────────
  SER  ->> RX:   byte stream into RxBuffer (2,048 B)
  RX   ->> RX:   RX_WAIT_AA: match 0xAA
  RX   ->> RX:   RX_WAIT_55: match 0x55
  RX   ->> RX:   RX_LEN_HI + RX_LEN_LO → rxLen = 512
  RX   ->> RX:   RX_MODE → rxModeByte = 0x00 (MODE_MASK)
  RX   ->> RX:   RX_PAYLOAD × 512: fill rxbuf[512]
  RX   ->> RX:   RX_CRC_HI + RX_CRC_LO → rxCrc
  RX   ->> RX:   RX_END_55 + RX_END_AA: match [55 AA]
  RX   ->> RX:   crc16_ccitt([0x00] + rxbuf[512]) vs rxCrc

  alt CRC matches → RX_OK
    RX   ->> RX:   memcpy(framebuf, rxbuf, 512 B)  [last-good cache updated]
    RX   ->> SER:  Serial.write(ACK=0x06)  1 B
    SER  -->> SP:  serial.read(1) → 0x06 within 50 ms<br/>frames_acked++
  else CRC mismatch → RX_CRC_ERROR
    RX   ->> SER:  Serial.write(NAK=0x15)  1 B
    SER  -->> SP:  0x15 → retry (up to 3× total)
  end

  Note over RX,REND: ── t ≈ 29 ms ── Render ──────────────────────────────────
  RX   ->> REND: currentMode = MODE_MASK · framebuf ready
  REND ->> REND: analogRead(GPIO34) → 12-bit ADC → EWMA filter → t ∈ [0.0, 1.0]
  REND ->> REND: renderMask(): loop i in 0..4095<br/>  byte = framebuf[i >> 3]<br/>  bit  = (byte >> (7-(i&7))) & 1<br/>  if bit: RGB = (255, (1-t)×255, (1-t)×255)<br/>  else:   RGB = (0, 0, 0)
  REND ->> DMA:  drawPixelRGB888(x, y, r, g, b)  ×4,096 calls

  Note over DMA,PNL: ── t ≈ 30 ms ── Display Output ──────────────────────────
  DMA  ->> PNL:  I2S DMA double-buffer → HUB75E 16-pin ribbon<br/>1/32 scan: rows 0–31 on R1G1B1, rows 32–63 on R2G2B2<br/>Row select via A B C D E (GPIO 23 19 5 17 32)<br/>Latch: LAT (GPIO 4) · Enable: OE (GPIO 15) · Clock: CLK (GPIO 16)
  Note right of PNL: 4,096 RGB LEDs illuminated<br/>128 × 128 mm active area<br/>5 V / 3 A from external PSU<br/>512 B → 4,096 colored pixels ✓
```

### Byte-count ledger per step

| Step | Data | Size |
|---|---|---|
| Raw camera frame | BGR uint8 640×480 | **921,600 B** |
| YOLO mask tensor (1 person) | float32 480×640 | **1,228,800 B** |
| Binary silhouette (640×480) | uint8 | **307,200 B** |
| Downscaled silhouette | uint8 64×64 | **4,096 B** |
| Bit-packed payload | Mode 0 | **512 B** |
| Full framed packet | SOF+LEN+MODE+payload+CRC+EOF | **521 B** |
| ACK/NAK reply | 1 byte | **1 B** |
| Mode-notify byte | ESP32 → host | **1 B** |
| framebuf cache | on ESP32 | **512 B** |
| Render calls | drawPixelRGB888 × 4,096 | **4,096 calls** |

### Timing budget at 30 fps (33.3 ms/frame)

| Phase | Estimated time |
|---|---|
| cap.read() | ~1 ms |
| YOLO GPU inference | ~10–40 ms |
| MediaPipe CPU | ~5 ms |
| Silhouette + resize | ~2 ms |
| pack + CRC + write | < 1 ms |
| Serial TX (521 B @ 1 Mbaud) | **4.2 ms** |
| ACK round-trip | ~1 ms |
| ESP32 render (4096 px) | ~2 ms |
| **Total (GPU path)** | **~26–56 ms** |

> ⚠️ YOLO is the dominant bottleneck. On Jetson with CUDA the GPU path runs ~15 ms/frame; on a Mac CPU it can exceed 40 ms, limiting effective TX rate below 25 fps.

### Module Dependency Graph

```mermaid
graph TD
  %% ── Entry Points ────────────────────────────────────────────────
  subgraph ENTRY["🚀 Entry Points"]
    VS["**vision_send.py**\n─────────────────\nmain() ← argparse\nautodetect_port()\nopen_camera()\nextract_fingertips()\ndraw_fingertips_camera()\ndraw_fingertips_grid()\nparse_args()"]

    VP["**vision.py**\n─────────────────\nmain() standalone\nopen_camera()\n❌ no serial sender\n[preview + save only]"]

    ORCH["**orchestrator.py**\n─────────────────\nrun_agent() async\nexecute_tool()\nwrite_file / read_file\n7× Claude agent swarm\nMODEL_ARCHITECT / CODER"]

    DA["**doc_agent.py**\n─────────────────\nHistorian agent\nArchitect agent  ← you are here\nCritic agent\ncollect_project_files()\nget_git_context()\ndb_connect() / db_save_*()"]
  end

  %% ── Core Protocol Library ───────────────────────────────────────
  subgraph PROTO["📦 serial_protocol.py — Public Interface"]
    SP_CLASS["**SerialSender** class\n─────────────────\n__init__(port, baudrate,\n  ack_timeout_s, max_retries)\nsend_mask(mask: ndarray) → bool\nsend_fingertips(tips: list) → bool\nread_mode_change() → int|None\nclose()\n─────────────────\nproperties: esp32_mode\ncounters: frames_sent/acked/naked"]

    SP_FN["**Free functions**\n─────────────────\nbuild_frame(mode, payload) → bytes\npack_mask(ndarray) → bytes [512 B]\nunpack_mask(bytes) → ndarray\npack_fingertips(list) → bytes\nunpack_fingertips(bytes) → list\ncrc16_ccitt(data, init) → int"]

    SP_TYPES["**Types / Constants**\n─────────────────\nFingertip(NamedTuple)\n  x, y, r, g, b: int\nPANEL_SIZE = 64\nPAYLOAD_BYTES = 512\nFRAME_START = AA 55\nFRAME_END   = 55 AA\nACK_BYTE = 0x06\nNAK_BYTE = 0x15\nMODE_MASK = 0x00\nMODE_FINGERTIPS = 0x01\nMODE_NOTIFY_0/1 = 0x10/0x11\nMAX_FINGERTIPS = 10"]
  end

  %% ── Doc / Sync system ───────────────────────────────────────────
  subgraph DOCS["📝 Documentation System"]
    GS["**gdrive_sync.py**\n─────────────────\ndetect_features()\nget_changed_files()\nsync_to_drive()\nappend_to_feature_doc()\ncompose_feature_section()\nfeature_to_filename()\nFEATURE_MAP dict\nRCLONE_REMOTE config"]

    GSU["**gdrive_setup.py**\n─────────────────\nmain()\n_create_remote()\nrclone OAuth browser flow\ntest sync to Drive"]
  end

  %% ── Firmware ────────────────────────────────────────────────────
  subgraph FW["⚡ firmware/me135_led_pot/"]
    MAIN_CPP["**main.cpp**\n─────────────────\nsetup() / loop()\npollFrame() RX state machine\nrenderMask()\nrenderFingertips()\nblankPanel()\ncrc16_ccitt() [C impl]\nFingertip struct\nRxState enum"]

    PINI["**platformio.ini**\n─────────────────\nplatform=espressif32@6.5.0\nboard=esp32dev\nframework=arduino\nmonitor_speed=115200"]
  end

  %% ── Python Package Dependencies ─────────────────────────────────
  subgraph PYLIBS["📦 Python Packages (vision/requirements.txt)"]
    YOLO_LIB["**ultralytics ≥ 8.0**\nYOLO class\nyolov8n-seg.pt ~6 MB\nauto-download on first run"]
    CV2_LIB["**opencv-python ≥ 4.8**\ncv2.VideoCapture\ncv2.resize / threshold\ncv2.findContours\ncv2.morphologyEx\ncv2.imshow / waitKey"]
    NP_LIB["**numpy ≥ 1.24**\nndarray · packbits\nmaximum · zeros\nascontiguousarray"]
    SER_LIB["**pyserial ≥ 3.5**\nserial.Serial\nSerial.write / read\nlist_ports.comports()"]
    MP_LIB["**mediapipe**\nmp.solutions.hands\nHands()\nprocess()"]
    ANT_LIB["**anthropic ≥ 0.40**\nAsyncAnthropic\nmessages.stream()\nclaude-opus/sonnet-4-6"]
  end

  %% ── C++ Library Dependencies ────────────────────────────────────
  subgraph CLIBS["📦 C++ / Arduino Libraries (platformio.ini lib_deps)"]
    HUB75_LIB["**ESP32-HUB75-MatrixPanel-DMA v3.0.11**\nMatrixPanel_I2S_DMA\ndrawPixelRGB888()\nfillScreenRGB888()\nI2S DMA HUB75E timing"]
    AGFX["**Adafruit GFX Library v1.11.9**"]
    ABUSIO["**Adafruit BusIO v1.16.1**"]
  end

  %% ── Import edges ────────────────────────────────────────────────

  %% vision_send.py imports
  VS -->|"from serial_protocol import\nFingertip · SerialSender\nMODE_MASK · MODE_FINGERTIPS"| SP_CLASS
  VS -->|"from serial_protocol import\nFingertip · MODE_* consts"| SP_TYPES
  VS -->|"import cv2"| CV2_LIB
  VS -->|"import numpy as np"| NP_LIB
  VS -->|"from ultralytics import YOLO"| YOLO_LIB
  VS -->|"import mediapipe as mp"| MP_LIB

  %% vision.py imports (standalone — NO serial_protocol)
  VP -->|"import cv2"| CV2_LIB
  VP -->|"import numpy as np"| NP_LIB
  VP -->|"from ultralytics import YOLO"| YOLO_LIB

  %% serial_protocol.py imports
  SP_CLASS -->|"import serial\nserial.tools.list_ports"| SER_LIB
  SP_CLASS -->|"import numpy"| NP_LIB
  SP_FN    -->|"import numpy"| NP_LIB
  SP_FN    -->|"import struct"| SP_TYPES

  %% doc_agent.py imports
  DA -->|"import anthropic"| ANT_LIB
  DA -->|"from gdrive_sync import\nsync_to_drive · detect_features\nget_changed_files"| GS

  %% orchestrator.py imports
  ORCH -->|"import anthropic"| ANT_LIB

  %% gdrive_sync → gdrive_setup relationship
  GS  -.->|"delegates rclone OAuth\n(user must run once)"| GSU

  %% firmware deps
  MAIN_CPP -->|"#include"| HUB75_LIB
  PINI     -->|"lib_deps"| HUB75_LIB
  PINI     -->|"lib_deps"| AGFX
  PINI     -->|"lib_deps"| ABUSIO
  HUB75_LIB -->|"depends"| AGFX
  HUB75_LIB -->|"depends"| ABUSIO

  %% ── Cross-system serial wire boundary ──────────────────────────
  SP_CLASS -.->|"⬇ USB-CDC 1 Mbaud\n521 B framed packet\nACK/NAK 1 B reply"| MAIN_CPP

  %% ── Styling ─────────────────────────────────────────────────────
  style SP_CLASS fill:#2d6a4f,color:#fff,stroke:#1b4332
  style SP_FN    fill:#2d6a4f,color:#fff,stroke:#1b4332
  style SP_TYPES fill:#2d6a4f,color:#fff,stroke:#1b4332
  style VS       fill:#1d3557,color:#fff,stroke:#457b9d
  style VP       fill:#1d3557,color:#fff,stroke:#457b9d
  style MAIN_CPP fill:#7b2d8b,color:#fff,stroke:#4a0e59
  style DA       fill:#6b3a1f,color:#fff,stroke:#3d1f0a
  style ORCH     fill:#6b3a1f,color:#fff,stroke:#3d1f0a
```

### Class / Interface Boundaries

| Boundary | Producer | Consumer | Contract |
|---|---|---|---|
| **Python → C firmware** | `SerialSender.send_mask()` | `pollFrame()` state machine | `[AA55][LEN][MODE][payload][CRC16][55AA]` · ACK=0x06 · NAK=0x15 |
| **ESP32 → Python** | `pollFrame()` button handler | `SerialSender.read_mode_change()` | Single byte `0x10` (mode 0) or `0x11` (mode 1) |
| **YOLO → Silhouette** | `model.predict()` GPU tensor | `np.maximum()` CPU loop | `result.masks.data` shape `(N, H, W)` float32 on GPU |
| **MediaPipe → Fingertips** | `hands.process()` CPU | `extract_fingertips()` | `hand_landmarks.landmark[i].{x,y,z}` normalized |
| **pack_mask → build_frame** | `pack_mask(ndarray)` | `build_frame(0x00, bytes)` | Exactly **512 B** or `ValueError` |
| **gdrive_sync → doc_agent** | `sync_to_drive()` | `da.main()` after agents | `list[tuple[str,str]]` sections + `list[dict]` proposals |

### Code Health Summary

**Overall Grade: B**

This is a well-structured, well-documented embedded + vision project with clear separation between the Python host pipeline, serial protocol, and ESP32 firmware. The serial framing protocol is solid — CRC-verified, ACK/NAK with retries, clean state machine on the ESP32 side. Documentation (`WIRING.md`) is exceptional for a course project.

**Strengths:** Clean serial protocol abstraction, defensive firmware (watchdog blanking, debounce, EWMA filtering), thorough hardware troubleshooting docs, proper async agent orchestration.

**Weaknesses:** A deploy-blocking missing dependency (`mediapipe`), stale README that documents wrong CRC scope, significant duplicated logic between `vision.py` and `vision_send.py`, and wasted compute running both ML models every frame regardless of active mode. No path traversal guard on the doc agent's file-read tool.

Two proposals are **must-fix** (PROP-001, PROP-003); the rest improve maintainability and performance with low risk.

### Improvement Proposals

**[PROP-002] YOLO + MediaPipe both run every frame regardless of active mode** — 🟢 low — nice-to-have

*Problem:* In `vision/vision_send.py::main()`, the YOLO segmentation pipeline AND the MediaPipe hand pipeline run on every frame unconditionally. When `current_mode == 0` (mask), the MediaPipe results are computed but never sent. When `current_mode == 1` (fingertips), the full YOLO predict + contour extraction runs but the mask is never sent. YOLO inference alone costs ~15-30ms/frame on CPU. This wastes ~50% of compute budget.

*Fix:* Gate each pipeline on `current_mode`. Only run `model.predict(...)` when `current_mode == MODE_MASK` and only run `hands.process(rgb)` when `current_mode == MODE_FINGERTIPS`. Keep the preview rendering for both modes if desired, but skip the heavy inference call for the inactive mode.

**[PROP-005] Duplicated `open_camera()` function and YOLO pipeline logic** — 🟢 low — nice-to-have

*Problem:* `vision/vision.py::open_camera()` and `vision/vision_send.py::open_camera()` are identical 8-line functions. The YOLO predict → mask merge → morphologyEx → findContours → clean pipeline (~30 lines) is also duplicated between both files. If the YOLO post-processing logic is updated in one file (e.g., changing `min_area` threshold or morph kernel), the other file silently diverges.

*Fix:* Extract `open_camera()` and a `process_yolo_frame(model, frame, confidence) -> (clean_mask, contours, boxes)` function into a shared module (e.g., `vision/pipeline.py`). Both `vision.py` and `vision_send.py` import from it.

**[CV-001] GPU→CPU tensor copy done unconditionally even when no person detected** — 🟢 low — nice-to-have

*Problem:* In vision_send.py, masks = result.masks.data.cpu().numpy() is only called inside `if result.masks is not None`, which is correct. But the result object itself (a full YOLO Results object) is always allocated on the GPU and transferred to host regardless of detections. On Jetson, the .cpu() call serialises the CUDA stream. When zero people are in frame, this copy is wasted every frame at ~30 fps.

*Fix:* Check result.masks is not None before building the Results object at all using YOLO's stream=True mode and processing results lazily. Alternatively, add an early-continue path: if result.boxes is None or len(result.boxes) == 0: skip the mask extraction block entirely and send a zero mask directly, avoiding the tensor transfer.

**[CV-002] vision.py and vision_send.py duplicate open_camera() and the YOLO inference block verbatim** — 🟢 low — must-fix

*Problem:* open_camera(), the YOLO predict loop, silhouette morphology, contour filtering, and the 64×64 resize are copy-pasted between vision.py (standalone preview) and vision_send.py (full sender). Any bug fix or parameter change must be applied twice. Already seen: vision.py uses a hardcoded confidence trackbar while vision_send.py uses argparse — the two have diverged.

*Fix:* Extract a shared vision_core.py module with: open_camera(), run_yolo_silhouette(frame, model, conf) → ndarray, run_mediapipe_fingertips(frame, hands) → list[Fingertip]. Both vision.py and vision_send.py import from it. vision.py becomes a thin preview wrapper; vision_send.py adds only the serial plumbing.

**[SERIAL-001] autodetect_port() silently picks the first port alphabetically — wrong on multi-device Jetson rigs** — 🟡 medium — nice-to-have

*Problem:* autodetect_port() does sorted(glob.glob(pat))[0]. On a Jetson with multiple USB-serial adapters (e.g., debug UART on /dev/ttyUSB0, ESP32 on /dev/ttyUSB1), the wrong device gets opened. The error only surfaces as a stream of NAK responses or garbled output, with no diagnostic message.

*Fix:* After opening the port, send a known probe byte (e.g., 0x00 + frame-end 0x55 0xAA) and listen for a NAK (0x15) within 200 ms. If no response, try the next candidate port. Log which port was selected and why. Alternatively, expose a --port-hint substring flag so users can write --port-hint ESP32.

---
