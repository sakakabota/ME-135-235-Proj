# ME135 Hardware Recommendation Report
## Camera-to-Display Human Detection System

| Field       | Value                                |
|------------ |--------------------------------------|
| Course      | ME135 — Intro to Mechatronic Design  |
| Instructor  | Prof. George Anwar                   |
| Semester    | Spring 2026, UC Berkeley             |

---

## 1. Recommended Bill of Materials

| # | Component                  | Qty | Est. Cost | Notes                                             |
|---|--------------------------- |-----|-----------|---------------------------------------------------|
| 1 | Sony PS3 Eye Camera (USB)  | 1   | $8        | 640×480 @ 60fps, Linux driver `gspca_ov534`       |
| 2 | **NVIDIA Jetson Orin Nano Super** | 1 | $249 | **CONFIRMED HARDWARE** — 1024 CUDA cores, 8 GB LPDDR5, JetPack 6, 67 TOPS |
| 3 | ESP32-DevKitC V4           | 1   | $10       | Dual-core 240 MHz, UART up to 5 Mbaud             |
| 4 | WS2812B LED Panel          | 1   | $30–80    | Addressable RGB; size depends on resolution subset |
| 5 | 5V / 30A PSU (LED panel)   | 1   | $20       | 60 mA/pixel × 500 pixels max = 30A for full white |
| 6 | USB-A cable                | 1   | $3        | Jetson ↔ PS3 Eye                                  |
| 7 | Jumper wires (M-M)         | 6   | $2        | UART TX/RX/GND + LED data + power                 |
| 8 | 3.3V↔5V level shifter      | 1   | $3        | GPIO 13 → WS2812B data (5V logic)                 |
| 9 | 1000 µF capacitor          | 1   | $1        | Across LED panel 5V/GND — inrush protection       |
|10 | 470 Ω resistor             | 1   | $0.10     | In series with LED data line                       |

**Total estimated cost: ~$230–280** (well within typical ME135 project budgets)

---

## 2. Platform: Jetson Orin Nano Super (confirmed)

| Feature          | Jetson Nano        | **Jetson Orin Nano Super** |
|----------------- |--------------------|----------------------------|
| CUDA cores       | 128 (Maxwell)      | **1024 (Ampere)**          |
| RAM              | 4 GB LPDDR4        | **8 GB LPDDR5**            |
| Memory bandwidth | 25.6 GB/s          | **102.4 GB/s**             |
| Power            | 5–10 W             | 7–25 W configurable        |
| AI performance   | ~472 GOPS          | **67 TOPS**                |
| JetPack          | 4.6 (Ubuntu 18.04) | **6.x (Ubuntu 22.04)**     |
| CUDA version     | 10.2               | **12.2**                   |
| OpenCV           | 4.1 (with CUDA)    | **4.8 (with CUDA)**        |
| CV perf (400×300)| ~6 ms/frame GPU    | **~2 ms/frame GPU**        |

> **Note:** OpenCV 4.8 on JetPack 6 uses return-value API style for CUDA ops.
> `gpu_accelerated.py` has been updated accordingly.

## 3. PS3 Eye Camera — Linux Setup

```bash
# Verify driver loaded
lsmod | grep gspca_ov534

# If not loaded:
sudo modprobe gspca_ov534

# Test capture
v4l2-ctl --device=/dev/video0 --set-fmt-video=width=640,height=480,pixelformat=YUYV
ffplay /dev/video0   # or use OpenCV VideoCapture(0)
```

**Known issues:**
- Auto-exposure can be aggressive on PS3 Eye; warm up 30+ frames before calibration.
- USB 2.0 bandwidth is sufficient for one camera at 640×480 YUYV but NOT for two.

## 4. ESP32 Wiring Diagram

```
                  Jetson Orin Nano Super          ESP32-DevKitC
                 ┌───────────┐                  ┌───────────┐
                 │    TX (UART1)──────────────►──│ RX GPIO16 │
                 │    RX (UART1)──◄──────────────│ TX GPIO17 │
                 │         GND ──────────────────│ GND       │
                 └───────────┘                  │           │
                                                │ GPIO13  ──┤──►[470Ω]──► WS2812B DIN
                                                │ GND     ──┤──► WS2812B GND
                                                └───────────┘
                                                               5V PSU ──► WS2812B VCC
                                                               PSU GND──► WS2812B GND
                                                                       ──► ESP32 GND
```

## 5. Power Budget

| Component          | Voltage | Current (max) |
|------------------- |---------|---------------|
| Jetson Orin Nano Super | 5V–20V | 25W max (set via nvpmodel) |
| ESP32              | 3.3V    | 0.5A          |
| WS2812B (500 LEDs) | 5V      | 30A (all white)|
| PS3 Eye (USB)      | 5V      | 0.3A          |

> Use separate supplies for the LED panel and the Jetson. Tie all GNDs together.

## 6. Risk Assessment (ME135 Requirement)

| Risk                         | Severity | Mitigation                              |
|----------------------------- |----------|-----------------------------------------|
| LED panel overcurrent        | High     | Fused PSU, current-limit code (brightness cap) |
| Jetson thermal throttle      | Medium   | Heat sink + fan, software temp monitor  |
| UART data corruption at 2Mbaud| Medium  | CRC-16 + ACK/NAK retransmit            |
| PS3 Eye USB disconnect       | Low      | Watchdog timer resets pipeline           |
| ESP32 firmware crash         | Low      | Hardware watchdog timer (5s)             |

---

*Prepared for ME135 Final Project — Spring 2026*
