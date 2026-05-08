# ME135 Real-Time Human Detection System
## UC Berkeley — MECENG 135 (Spring 2026) — Prof. George Anwar

> **One-liner:** PS3 Eye camera → Jetson (GPU CV) → Serial → ESP32 → Waveshare RGB-Matrix-P2 64×64 (HUB75)
>
> **⚠ Hardware update — 2026-05-07:** the output device is now a [Waveshare RGB-Matrix-P2 64×64](https://www.waveshare.com/wiki/RGB-Matrix-P2-64x64) HUB75 panel. Sections of this README and the sibling files (`esp32_main.cpp`, `serial_protocol.py`, `hardware_recommendation.md`, `cv_pipeline.py`, `gpu_accelerated.py`) still reference the legacy WS2812B / 400×300 / 108×108 / 15 KB-frame design and are stale. The new transport is **64×64 = 512 bytes/frame bit-packed**, and the ESP32 drives the panel via `ESP32-HUB75-MatrixPanel-DMA` instead of FastLED.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [System Architecture](#2-system-architecture)
3. [File Dependency Tree](#3-file-dependency-tree)
4. [Quick Start (5 Steps)](#4-quick-start-5-steps)
5. [Key Configuration Parameters](#5-key-configuration-parameters)
6. [Cross-File Consistency Audit](#6-cross-file-consistency-audit)
7. [Known Issues & TODOs](#7-known-issues--todos)
8. [ME135 Deliverables Checklist](#8-me135-deliverables-checklist)

---

## 1. Project Overview

This system captures video from a **Sony PS3 Eye camera** (640×480 @ 60 fps),
performs real-time **background subtraction** to detect humans, produces a
**64×64 binary pixel matrix** (0 = background, 1 = human) — matching the panel's
native resolution — bit-packs it into **512 bytes**, and transmits it over
**UART at 921600 bps (or higher)** to an **ESP32** that drives a
**Waveshare RGB-Matrix-P2 64×64** LED panel via the **HUB75** interface.

An optional **LabVIEW IoT dashboard** can monitor system state over TCP.

### Data Flow

```
PS3 Eye (USB)          NVIDIA Jetson              ESP32                  Display
  640×480     ──USB──►  Background Sub.  ──UART──►  CRC verify    ──HUB75──►  RGB-Matrix-P2
  @ 60 fps              + Threshold       921600+   + Unpack 64×64             64×64 panel
                        + Resize 64×64               + DMA push                (Waveshare)
                        + Bit-pack 512B
                                                         │
                                                         ▼  (TCP, optional)
                                                    LabVIEW IoT Hub
```

---

## 2. System Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                        NVIDIA JETSON (Host)                          │
│                                                                      │
│  ┌────────────┐    ┌──────────────────┐    ┌────────────────────┐   │
│  │ config.yaml│───►│     main.py      │    │  serial_protocol.py│   │
│  │  (params)  │    │  (orchestrator)  │───►│  (bit-pack + UART) │   │
│  └────────────┘    │                  │    └─────────┬──────────┘   │
│                    │  if use_gpu=true  │              │              │
│                    │    ┌─────────┐    │              │  TX/RX/GND   │
│                    │    │GPU      │    │              │              │
│                    ├───►│Pipeline │    │              │              │
│                    │    └─────────┘    │              │              │
│                    │  else             │              │              │
│                    │    ┌─────────┐    │              │              │
│                    ├───►│CPU      │    │              │              │
│                    │    │Pipeline │    │              │              │
│                    │    └─────────┘    │              │              │
│                    └──────────────────┘              │              │
│                                                      │              │
└──────────────────────────────────────────────────────┼──────────────┘
                                                       │ UART 2Mbaud
                                          ┌────────────▼──────────────┐
                                          │     ESP32-DevKitC         │
                                          │                           │
                                          │  ┌────────────────────┐   │
                                          │  │  esp32_main.cpp    │   │
                                          │  │  - Frame sync      │   │
                                          │  │  - CRC-16 verify   │   │
                                          │  │  - ACK / NAK       │   │
                                          │  │  - NeoPixel driver  │   │
                                          │  └────────┬───────────┘   │
                                          │           │ GPIO 13       │
                                          └───────────┼───────────────┘
                                                      │
                                          ┌───────────▼───────────────┐
                                          │   WS2812B LED Panel       │
                                          │   (addressable RGB)       │
                                          └───────────────────────────┘
```

### Wire-Protocol Summary (see `PROTOCOL_SPEC.md`)

```
 ┌──────┬──────┬─────────┬───────────────┬────────┬──────┐
 │ 0xAA │ 0x55 │ LEN (2B)│ PAYLOAD (15KB)│ CRC(2B)│0x55AA│
 └──────┴──────┴─────────┴───────────────┴────────┴──────┘
  Start         Big-end.   Bit-packed      CCITT     End
  marker        uint16     400×300 matrix  FALSE     marker
                = 15000
```

---

## 3. File Dependency Tree

```
me135-human-detection/
│
├── config.yaml                  ← Central config (SINGLE SOURCE OF TRUTH)
├── requirements.txt             ← Python dependencies
├── PROJECT_README.md            ← This file
├── SETUP.md                     ← Detailed setup walkthrough
├── PROTOCOL_SPEC.md             ← Serial protocol v1.0 specification
├── hardware_recommendation.md   ← BOM, wiring, risk assessment
│
├── main.py                      ← Entry point (orchestrator)
│   ├── imports cv_pipeline.py   ←── CVPipeline  (CPU path)
│   ├── imports gpu_accelerated.py ← GPUPipeline (GPU path, + CUDA_AVAILABLE flag)
│   └── imports serial_protocol.py ← SerialSender (UART framing)
│
├── cv_pipeline.py               ← CPU background-subtraction pipeline
│   └── uses: cv2, numpy
│
├── gpu_accelerated.py           ← CUDA-accelerated pipeline (Jetson)
│   └── uses: cv2.cuda, numpy
│
├── serial_protocol.py           ← Bit-packing, CRC-16, UART send/recv
│   └── uses: numpy, pyserial, struct
│
└── firmware/
    ├── esp32_main.cpp           ← ESP32 Arduino firmware
    │   └── uses: Adafruit_NeoPixel
    └── platformio.ini           ← PlatformIO build configuration
```

### Import Graph (Python)

```
main.py
  ├── yaml          (PyYAML)         — load config.yaml
  ├── cv2           (OpenCV)         — preview window
  ├── numpy                          — matrix ops
  ├── cv_pipeline ──────────────► CVPipeline
  │     ├── cv2                        class: calibrate(), process_frame(), release()
  │     └── numpy
  ├── gpu_accelerated ──────────► GPUPipeline, CUDA_AVAILABLE
  │     ├── cv2.cuda                   class: calibrate(), process_frame(), release()
  │     └── numpy
  └── serial_protocol ──────────► SerialSender
        ├── serial  (pyserial)         class: send_frame(matrix), close()
        ├── numpy                      func:  pack_matrix(), unpack_matrix(), crc16_ccitt()
        └── struct
```

---

## 4. Quick Start (5 Steps)

```
┌─────────────────────────────────────────────────────────────┐
│  Step 1:  Install dependencies                              │
│           pip install -r requirements.txt                    │
│                                                             │
│  Step 2:  Flash ESP32 firmware                              │
│           cd firmware/ && pio run -t upload                  │
│                                                             │
│  Step 3:  Wire Jetson ↔ ESP32 ↔ LED panel                  │
│           (See hardware_recommendation.md §4)               │
│                                                             │
│  Step 4:  Edit config.yaml                                  │
│           Set camera.device_index, serial.port, use_gpu     │
│                                                             │
│  Step 5:  Run!                                              │
│           python3 main.py                                   │
│           (or:  python3 main.py --no-serial --show-preview) │
└─────────────────────────────────────────────────────────────┘
```

> **Debug mode** (no hardware): `python3 main.py --no-serial --show-preview`
> opens a live window showing detected humans overlaid in green.

---

## 5. Key Configuration Parameters

All parameters live in **`config.yaml`**. The table below lists the most
important ones grouped by subsystem.

| Section        | Key                    | Default         | Description                                       |
|--------------- |----------------------- |-----------------|---------------------------------------------------|
| `camera`       | `device_index`         | `0`             | `/dev/video<N>` for PS3 Eye                       |
| `camera`       | `capture_fps`          | `60`            | PS3 Eye max; lower to reduce CPU load             |
| `camera`       | `warmup_frames`        | `30`            | Discarded frames for auto-exposure settling        |
| `calibration`  | `method`               | `"mog2"`        | `"mog2"` · `"knn"` · `"static_median"`           |
| `calibration`  | `num_frames`           | `120`           | Frames used to build background model              |
| `processing`   | `output_width`         | `400`           | Binary matrix columns — must match ESP32           |
| `processing`   | `output_height`        | `300`           | Binary matrix rows — must match ESP32              |
| `processing`   | `threshold`            | `25`            | Pixel-diff threshold (static_median method)        |
| `processing`   | `morph_kernel_size`    | `5`             | Morphological open/close kernel (px)               |
| `processing`   | `min_contour_area`     | `500`           | Minimum blob area to keep (px²)                    |
| `processing`   | `use_gpu`              | `true`          | `true` = CUDA on Jetson; `false` = CPU fallback   |
| `serial`       | `port`                 | `/dev/ttyUSB0`  | ESP32 serial port on Jetson                        |
| `serial`       | `baud_rate`            | `2000000`       | 2 Mbaud — must match ESP32 firmware                |
| `serial`       | `max_retries`          | `3`             | Retransmit attempts per frame on NAK/timeout       |
| `display`      | `fps_target`           | `10`            | Display refresh cap (frames/sec)                   |
| `display`      | `brightness`           | `128`           | WS2812B brightness (0-255)                         |
| `safety`       | `watchdog_timeout_s`   | `5.0`           | Halt/blank if no frame processed within window     |
| `safety`       | `max_serial_errors`    | `10`            | Consecutive UART failures → safety shutdown        |
| `safety`       | `max_cpu_temp_c`       | `80`            | Jetson thermal throttle safeguard (not yet impl.)  |
| `labview`      | `enabled`              | `false`         | Enable LabVIEW IoT hub TCP link                    |

---

## 6. Cross-File Consistency Audit

All files were authored by a single integration pass. The following critical
constants were verified **identical** across every file that references them:

| Constant              | config.yaml | serial_protocol.py | esp32_main.cpp | PROTOCOL_SPEC.md |
|---------------------- |:-----------:|:------------------:|:--------------:|:----------------:|
| Matrix 400×300        | ✅          | ✅                 | ✅             | ✅               |
| Payload = 15,000 B    | —           | ✅                 | ✅             | ✅               |
| Baud = 2,000,000      | ✅          | (from cfg) ✅      | ✅             | ✅               |
| Start = 0xAA 0x55     | ✅          | ✅                 | ✅             | ✅               |
| End = 0x55 0xAA       | ✅          | ✅                 | ✅             | ✅               |
| ACK = 0x06            | —           | ✅                 | ✅             | ✅               |
| NAK = 0x15            | —           | ✅                 | ✅             | ✅               |
| CRC-16 CCITT-FALSE    | —           | ✅                 | ✅             | ✅               |
| CRC init = 0xFFFF     | —           | ✅                 | ✅             | ✅               |
| CRC poly = 0x1021     | —           | ✅                 | ✅             | ✅               |
| Bit order = MSB-first | —           | ✅ (`big`)         | ✅             | ✅               |
| Watchdog = 5 s        | ✅          | —                  | ✅ (5000ms)    | —                |
| LED pin = GPIO 13     | ✅          | —                  | ✅             | —                |
| UART RX/TX = 16/17    | —           | —                  | ✅             | ✅               |
| Max retries = 3       | ✅          | (from cfg) ✅      | —              | ✅               |
| LED brightness = 128  | ✅          | —                  | ✅             | —                |

### Python import/naming verification

| main.py imports              | Source file           | Symbol       | Match? |
|----------------------------- |---------------------- |------------- |--------|
| `from cv_pipeline import`    | `cv_pipeline.py`      | `CVPipeline` | ✅     |
| `from gpu_accelerated import`| `gpu_accelerated.py`  | `GPUPipeline`, `CUDA_AVAILABLE` | ✅ |
| `from serial_protocol import`| `serial_protocol.py`  | `SerialSender` | ✅   |

### API contract alignment (CVPipeline vs GPUPipeline)

| Method           | CVPipeline | GPUPipeline | Signature match? |
|----------------- |:----------:|:-----------:|:----------------:|
| `__init__(cfg)`  | ✅         | ✅          | ✅               |
| `calibrate()`    | ✅         | ✅          | ✅               |
| `process_frame()`| → (ndarray \| None, ndarray \| None) | same | ✅ |
| `release()`      | ✅         | ✅          | ✅               |

---

## 7. Known Issues & TODOs

### 🔴 Critical

| # | Issue | File(s) | Details |
|---|-------|---------|---------|
| 1 | **ESP32 RAM overflow at 120K LEDs** | `esp32_main.cpp` | `Adafruit_NeoPixel strip(120000, …)` allocates 120,000 × 3 = **360 KB** for the pixel buffer. ESP32 has only ~520 KB total SRAM. The firmware will crash or fail to allocate. **Fix:** Set `LED_COUNT` to the *actual* panel size (e.g., 1,024 for a 32×32 panel) and implement a downscaling/tile-mapping strategy. |
| 2 | **WS2812B refresh too slow at 120K** | `esp32_main.cpp` | WS2812B protocol is ~30 µs/pixel → 120,000 pixels = **3.6 seconds** per `strip.show()`. Even 10,000 pixels = 300 ms. Display size must be chosen carefully to stay under 100 ms. **Max practical: ~3,000 LEDs for 10 fps target.** |

### 🟡 Medium Priority

| # | Issue | File(s) | Details |
|---|-------|---------|---------|
| 3 | **Serial throughput caps at ~8 fps** | `PROTOCOL_SPEC.md`, `serial_protocol.py` | At 2 Mbaud, one 15,008-byte frame takes ~75 ms. With 50 ms ACK window, worst case is ~8 fps. To hit 10 fps, **pipeline the TX** (send while processing next frame) using a background thread. |
| 4 | **LabVIEW IoT hub not implemented** | `config.yaml`, `main.py` | Config has `labview.enabled` but no code reads it. ESP32 heartbeat path is not implemented. **TODO:** Add TCP socket client in main.py and heartbeat sender in ESP32 firmware. |
| 5 | **GPU KNN falls back to CUDA MOG2** | `gpu_accelerated.py` | OpenCV CUDA does not have a KNN background subtractor. If `method: "knn"` is selected with `use_gpu: true`, the GPU pipeline silently uses MOG2 instead. **TODO:** Log a warning when this substitution occurs. |
| 6 | **Jetson thermal monitoring not implemented** | `config.yaml` | `safety.max_cpu_temp_c` is defined (80°C) but no code reads `/sys/devices/virtual/thermal/` to enforce it. **TODO:** Add thermal check in main loop. |
| 7 | **`cv2.cuda.cvtColor` API variance** | `gpu_accelerated.py` | Some OpenCV CUDA builds use `dst = cv2.cuda.cvtColor(src, code)` while others accept a `dst` parameter. Test on target Jetson and adjust. |

### 🟢 Low Priority / Cleanup

| # | Issue | File(s) | Details |
|---|-------|---------|---------|
| 8 | **Unused import: `time`** | `cv_pipeline.py` | `import time` present but never used. Remove. |
| 9 | **Unused variable: `rxBuffer`** | `esp32_main.cpp` | `static uint8_t rxBuffer[FRAME_TOTAL_SIZE]` declared but never read/written; receive logic uses `payload[]` directly. Remove. |
| 10 | **Unused pip deps: `imutils`, `tqdm`** | `requirements.txt` | Listed but not imported anywhere. Either use them (e.g., tqdm for calibration progress bar) or remove to keep deps minimal. |
| 11 | **No unit tests** | — | `pytest` in requirements but no `tests/` directory. **TODO:** Add tests for `pack_matrix`/`unpack_matrix` round-trip and `crc16_ccitt` against known vectors. |
| 12 | **Split-flap display path** | `config.yaml`, `esp32_main.cpp` | `display.type` supports `"split_flap"` in config but ESP32 firmware only handles WS2812B. **TODO:** Implement split-flap driver or remove option. |

### Summary Scorecard

```
 🔴 Critical ........  2 issues  (must fix before demo)
 🟡 Medium ..........  5 issues  (should fix for robustness)
 🟢 Low / Cleanup ...  5 issues  (nice-to-have polish)
 ──────────────────────────────
 Total ..............  12 issues
```

---

## 8. ME135 Deliverables Checklist

Per the ME135 syllabus (Prof. Anwar), the final project requires:

| Deliverable              | Status    | File / Location                                  |
|------------------------- |-----------|--------------------------------------------------|
| CAD schematics           | ⬜ TODO   | Not yet created — use KiCad or Fritzing          |
| Circuit diagrams         | ✅ Partial | ASCII wiring in `hardware_recommendation.md` §4  |
| Software flowcharts      | ✅ Partial | Architecture diagram in this README §2            |
| V&V matrix               | ⬜ TODO   | Verification: bench test CRC, pack/unpack round-trip; Validation: live demo with humans |
| Risk assessment          | ✅ Done   | `hardware_recommendation.md` §6                  |
| Hardware failsafes       | ✅ Done   | Watchdog in ESP32 firmware + Python safety loop   |
| Software watchdog timers | ✅ Done   | `main.py` (5s timeout) + `esp32_main.cpp` (5s)   |
| Final demo               | ⬜ TODO   | Prepare live walkthrough with LED panel           |
| Presentation             | ⬜ TODO   | Slide deck summarising architecture + results     |

---

## Appendix A — Running Without Hardware (Simulation)

For development on a laptop without Jetson/ESP32/camera:

```bash
# 1. Use a webcam instead of PS3 Eye (device 0 is usually the laptop cam)
# 2. Disable GPU and serial:
python3 main.py --no-serial --show-preview --config config.yaml
#    (set processing.use_gpu: false in config.yaml first)
```

## Appendix B — Protocol Quick-Test (Loopback)

```python
# In a Python REPL — verify pack/unpack round-trip:
from serial_protocol import pack_matrix, unpack_matrix, crc16_ccitt
import numpy as np

# Random binary matrix
m = np.random.randint(0, 2, size=(300, 400), dtype=np.uint8)
packed = pack_matrix(m)
assert len(packed) == 15000
recovered = unpack_matrix(packed)
assert np.array_equal(m, recovered), "Round-trip FAILED"
print("✅ pack/unpack round-trip passed")

# CRC test vector
assert crc16_ccitt(b"123456789") == 0x29B1, "CRC FAILED"
print("✅ CRC-16/CCITT-FALSE correct")
```

---

*Integration document prepared by the ME135 Integration Architect.*
*All 11 deliverable files verified for cross-file consistency.*
*Last updated: Spring 2026*
