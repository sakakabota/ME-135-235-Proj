# ME135 Human Detection System — Setup Guide

## Prerequisites

| Component         | Version / Spec               |
|------------------ |------------------------------|
| Jetson Nano/Orin  | JetPack ≥ 4.6               |
| Python            | 3.10+                        |
| OpenCV            | ≥ 4.8 (CUDA build for GPU)  |
| PlatformIO CLI    | ≥ 6.0                        |
| PS3 Eye Camera    | Connected via USB             |
| ESP32-DevKitC     | Connected via USB-to-serial   |

---

## Step 1 — Clone & Install Python Dependencies

```bash
git clone https://github.com/your-team/me135-human-detection.git
cd me135-human-detection

# Create virtual environment (optional but recommended)
python3 -m venv .venv && source .venv/bin/activate

pip install -r requirements.txt
```

> **Jetson Note:** Do NOT install `opencv-contrib-python` from pip — use the
> system OpenCV that ships with JetPack (it has CUDA support compiled in).
> Comment out the opencv line in `requirements.txt` on Jetson.

## Step 2 — Verify Camera

```bash
# Check driver
lsmod | grep gspca_ov534
# If missing:
sudo modprobe gspca_ov534

# Quick test
python3 -c "import cv2; cap=cv2.VideoCapture(0); ok,f=cap.read(); print('Camera OK' if ok else 'FAIL'); cap.release()"
```

## Step 3 — Flash ESP32 Firmware

```bash
cd firmware/   # directory containing esp32_main.cpp + platformio.ini

# Install PlatformIO (if not already)
pip install platformio

# Build & upload
pio run -t upload

# Monitor debug output
pio device monitor -b 115200
```

## Step 4 — Wire the Hardware

See `hardware_recommendation.md` §4 for the full wiring diagram.

**Quick connections:**
| Jetson      | ESP32        |
|------------ |--------------|
| UART1 TX    | GPIO 16 (RX) |
| UART1 RX    | GPIO 17 (TX) |
| GND         | GND          |

| ESP32       | LED Panel    |
|------------ |--------------|
| GPIO 13     | DIN (via 470Ω resistor) |
| GND         | GND          |
| —           | 5V from PSU  |

## Step 5 — Configure & Run

```bash
# Edit config.yaml for your setup
#   - camera.device_index: check /dev/video*
#   - serial.port: check /dev/ttyUSB* or /dev/ttyACM*
#   - processing.use_gpu: true (Jetson) or false (laptop testing)

# Run with serial output
python3 main.py

# Run without ESP32 (debug mode with preview window)
python3 main.py --no-serial --show-preview

# Run with custom config
python3 main.py --config my_config.yaml --show-preview
```

## Troubleshooting

| Symptom                      | Fix                                               |
|----------------------------- |---------------------------------------------------|
| `Camera read failed`         | Check `/dev/video0`, try `sudo modprobe gspca_ov534` |
| `Serial init failed`        | Check `serial.port` in config.yaml, run `ls /dev/ttyUSB*` |
| Low FPS on Jetson            | Set `use_gpu: true`, verify `cv2.cuda.getCudaEnabledDeviceCount() > 0` |
| CRC errors on ESP32          | Check baud rate matches (2000000 both sides), check wiring |
| ESP32 watchdog blanks screen | Jetson not sending frames — check pipeline status |
| `ImportError: cv2.cuda`      | OpenCV not built with CUDA — use JetPack's system OpenCV |

---

*ME135 Spring 2026 — UC Berkeley*
