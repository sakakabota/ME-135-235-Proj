# ME135/235 Final Project — Pixel Mirror

Real-time human silhouette and fingertip tracking, rendered live on a 64×64
LED matrix.

A laptop captures USB-camera frames, runs YOLOv8-seg filtered to the person
class, and packs the resulting 64×64 binary mask into 512 bytes. Those bytes
travel over UART to an ESP32, which drives a Waveshare RGB-Matrix-P2 panel
through the HUB75 interface. A potentiometer on the ESP32 side lerps the
silhouette from white to red while the program runs. A pushbutton switches
the system between silhouette mode and a MediaPipe fingertip-dot mode, and
the ESP32 announces the change to the host so the dashboard preview matches
what the panel is actually showing.

## Quick navigation

The deliverables in this repo mirror §6 of the report one-for-one. The
table below is the fastest way to find a given component:

| Path | What it is |
|---|---|
| [`Kyle/vision/vision.py`](Kyle/vision/vision.py) | Standalone CV preview, no serial. Run this first to confirm the camera and the YOLO model are healthy. |
| [`Kyle/vision/vision_send.py`](Kyle/vision/vision_send.py) | Full host pipeline. YOLO silhouette plus MediaPipe fingertips, auto-detects the ESP32 serial port. |
| [`Kyle/vision/serial_protocol.py`](Kyle/vision/serial_protocol.py) | Framed UART protocol: CRC-16/CCITT, ACK/NAK with retries, mode byte. Shared by the standalone script and the dashboard. |
| [`Kyle/vision/dashboard/`](Kyle/vision/dashboard/) | PyQt6 dashboard (report §2). `POT working.py` is the runnable entry; `serial_worker.py` is the Qt-threaded TX worker. |
| [`Kyle/firmware/POT_working/`](Kyle/firmware/POT_working/) | PlatformIO project for the firmware. Entry point: `src/main.cpp`. |
| [`Kyle/firmware/WIRING.md`](Kyle/firmware/WIRING.md) | Pin map, bring-up procedure, and a troubleshooting table. Read this *before* you connect the panel. |
| [`Larry/`](Larry/) | Larry's reference CV implementations: `vision.py` (Python) and `vision_fast.cpp` (C++ / ONNX-DNN). Preview-only — no serial. |
| [`Steph/`](Steph/) | Steph's GUI design-review iteration: `Project_GUI.py` and the screen capture used in the report. |
| [`yolov8n-seg.pt`](yolov8n-seg.pt) | YOLOv8 nano segmentation checkpoint (~6.7 MB). Pre-staged so the first run doesn't have to download it. |

## How to run

### 1. CV preview only, no hardware needed

```bash
cd Kyle/vision
pip install -r requirements.txt
python vision.py
```

A camera window opens. Drag the `Conf %` slider to tune YOLO confidence.
`q` quits, `s` saves the current 64×64 mask as a PNG, `SPACE` pauses.

### 2. Full pipeline with the ESP32

1. Flash the firmware. From `Kyle/firmware/POT_working/`:
   ```bash
   pio run -t upload
   ```
   On a first run PlatformIO will pull the ESP32 toolchain and the
   `ESP32-HUB75-MatrixPanel-I2S-DMA` library automatically.

2. Wire the panel using [`Kyle/firmware/WIRING.md`](Kyle/firmware/WIRING.md).
   The pot and button are optional for the silhouette demo. Without them
   the system runs mode 0 at full red.

3. Plug in the ESP32 and start the host:
   ```bash
   cd Kyle/vision
   python vision_send.py
   ```
   The script auto-detects the serial port. If it picks the wrong one, pass
   `--port /dev/cu.usbserial-XXX` explicitly. Press the ESP32 button to
   toggle silhouette ↔ fingertip mode.

### 3. PyQt6 dashboard

```bash
cd Kyle/vision/dashboard
pip install -r requirements.txt
python "POT working.py"
```

The dashboard is the version pictured in §2. It carries a port picker, a
live LED preview, threshold sliders, and a link-stats readout. The TX path
runs in a worker thread but uses the same `SerialSender` as the standalone
script, so the ACK/NAK contract is identical.

## Wire protocol

Each 64×64 binary mask is bit-packed into 512 bytes (`np.packbits`,
row-major, MSB-first) and wrapped in
`[AA 55][LEN_H LEN_L][MODE][payload][CRC_H CRC_L][55 AA]`. The CRC is
CRC-16/CCITT-FALSE computed over the mode byte plus the payload. On a clean
frame the ESP32 replies `0x06`; on a CRC or sync error it replies `0x15`,
and the host retries up to three times before giving up. Mode `0x00` is the
silhouette; mode `0x01` is a fingertip packet (up to ten tips, five bytes
each: `x y r g b`). The ESP32 also emits sideband bytes outside the framing
— `0x10` / `0x11` when the mode-toggle button is pressed, and `0x20 <val>`
each time the pot reading moves by one count. That's what lets the
dashboard's preview track the panel without doing its own pot read. §3 of
the report has the full byte-level diagram.

## Repo layout

```
Kyle/
  vision/                 host-side Python (CV + protocol)
    dashboard/            PyQt6 GUI
    vision.py             standalone preview
    vision_send.py        full pipeline -> ESP32
    serial_protocol.py    framed protocol + SerialSender
  firmware/
    POT_working/          PlatformIO ESP32 firmware
    WIRING.md             pin map + bring-up procedure
Larry/                    reference CV implementations
Steph/                    GUI design-review iteration
yolov8n-seg.pt            YOLOv8 segmentation model checkpoint
checker_tile.png          calibration tile used by the GUI
```

## Hardware

- **Compute:** any laptop with a USB camera. A discrete GPU helps but isn't
  required; the nano model runs around 30 fps on CPU on a recent MacBook.
- **Microcontroller:** Adafruit ESP32 Feather V2 (PICO-MINI-02 module).
- **Display:** Waveshare RGB-Matrix-P2 64×64, HUB75E interface, 2 mm pitch.
  The pin map in `WIRING.md` is Feather-V2-specific: three pins had to be
  remapped because GPIO 16, 17, and 23 aren't broken out on this board.
- **Pot:** 10 kΩ linear on GPIO 33 (ADC1_CH5).
- **Button:** any momentary switch on GPIO 34, with an **external 3.3 V
  pull-up**. GPIO 34 is input-only and has no internal pull-up, so wire one
  in or the firmware will see floating-pin noise instead of presses.

## Debugging

A few things bit us during the build, so they're worth flagging up front.

- **PyQt6 won't load the cocoa plugin on macOS.** If the dashboard or
  `vision.py` dies with `could not find or load the Qt platform plugin "cocoa"
  in ""`, the cause is almost always a version skew between `PyQt6` and
  `PyQt6-Qt6`. Pin them: `pip install PyQt6==6.9.1 PyQt6-Qt6==6.9.0`. Don't
  bother setting `QT_QPA_PLATFORM_PLUGIN_PATH`; that path is a dead end here.
- **Wrong serial port.** `vision_send.py` auto-detects by VID/PID, but if you
  have more than one USB-serial device plugged in it can pick the wrong one
  and just sit there. Pass `--port /dev/cu.usbserial-XXX` (or `COM7` on
  Windows) to force it. `ls /dev/cu.*` on macOS lists candidates.
- **No frames on the panel but the host says it's sending.** Check the ACK
  rate in the dashboard's link-stats. If it's near zero, the ESP32 isn't
  seeing the start-of-frame bytes — usually a baud mismatch or a TX/RX swap.
  The firmware runs at 921600.
- **Button reads as constantly pressed.** GPIO 34 is input-only with no
  internal pull-up. Without an external 3.3 V pull-up the pin floats and the
  ISR fires on noise. The fix is a 10 kΩ resistor to 3.3 V, not a firmware
  workaround.
- **YOLO download hangs on the first run.** The repo pre-stages
  `yolov8n-seg.pt` so ultralytics shouldn't have to fetch anything, but if
  it does and the network is slow, kill it and copy the checkpoint into
  `~/.cache/ultralytics/` by hand.
- **First frame is blank, every frame after looks fine.** Known quirk of the
  HUB75 DMA driver — it needs one warm-up frame before the buffer is valid.
  We just send a zeroed mask at startup and move on.

## Acknowledgments

The ME135/235 teaching staff for the lab time and the equipment loans.
Larry and Steph carried the parallel CV and GUI tracks, and the deliverable
map at the top of this README points to their files at the paths the
report names. If a path in the report doesn't resolve in this repo, please
let us know; nothing was meant to drift.
