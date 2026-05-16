# Pixel Mirror — Wiring & Bring-Up

## 1. Summary

A Mac runs vision over a webcam, sends a 64×64 silhouette mask (or a short fingertip list) over USB serial to an Adafruit ESP32 **Feather V2**, which drives a Waveshare 64×64 HUB75 LED panel via I²S DMA. A potentiometer on the ESP32 controls a white→red color lerp on the displayed mask; a tactile button toggles between silhouette mode and finger-gloving mode.

This document is the bring-up reference for the production hardware in `Kyle/firmware/POT_working/`. The earlier DevKitC iteration is archived; do not use the old pin map.

## 2. Block diagram

```
   Mac (USB)             ESP32 Feather V2              HUB75 ribbon         Panel
  +--------+           +-------------+               +------------+      +---------+
  | vision |== USB ====| GPIOs (3.3V)|====16-pin=====|  HUB75 IN  |      | 64x64   |
  | _send  |           |             |               +------------+      | RGB     |
  +--------+           |             |                                   | 4096 LED|
                       | GPIO 33  <--+-- pot wiper                       +----+----+
                       | GND --------+-- pot term 1                           |
                       | 3V3 --------+-- pot term 3                           |
                       |             |                                        |
                       | GPIO 34  <--+-- button (active-low, ext. pull-up)    |
                       | GND --------+-- button other side                    |
                       |             |                                        |
                       | GND ============== common ground ==================  |
                       +-------------+                                        |
                                                       5V/3A PSU =============+
                                                                  V+   GND
```

Common ground: ESP32 GND ↔ 5V PSU GND ↔ HUB75 GND pins. Mandatory.

## 3. ESP32 ↔ HUB75 pinout (Feather V2)

16-pin IDC (2×8, 0.1") into the panel header labeled **IN**. Pin numbering matches the Waveshare RGB-Matrix-P2 64×64 silkscreen (pin 1 bottom-right, pin 16 top-left).

Three GPIOs that DevKitC tutorials assume free are unavailable on the Feather V2 and have been remapped — see notes.

| HUB75 pin | Signal | ESP32 GPIO | Feather V2 silk | Notes |
|---|---|---|---|---|
| 1 | GND | GND | GND | tie to ESP32 GND |
| 2 | OE | GPIO 15 | — | library default |
| 3 | LAT | GPIO 4 | A5 | library default |
| 4 | CLK | **GPIO 22** | SDA | **remapped** — GPIO 16 reserved for PSRAM on V2 |
| 5 | D | **GPIO 20** | SCL | **remapped** — GPIO 17 reserved for PSRAM on V2 |
| 6 | C | GPIO 5 | SCK | library default |
| 7 | B | GPIO 19 | MO | library default |
| 8 | A | **GPIO 21** | MI | **remapped** — GPIO 23 not broken out on V2 |
| 9 | E | GPIO 32 | — | **REQUIRED** for 64×64 (1/32 scan) |
| 10 | B2 | GPIO 13 | — | shares the onboard red LED (cosmetic mirroring only) |
| 11 | G2 | GPIO 12 | — | library default — **strapping pin, see §6** |
| 12 | R2 | GPIO 14 | — | library default |
| 13 | GND | GND | GND | second panel ground; tie both |
| 14 | B1 | GPIO 27 | — | library default |
| 15 | G1 | GPIO 26 | — | library default |
| 16 | R1 | GPIO 25 | — | library default |

The pin defines that live in `Kyle/firmware/POT_working/src/main.cpp` are the authoritative source. This table is generated from them — if they ever diverge, the firmware wins.

## 4. Power wiring

- Panel power: VH4 4-pin connector → 5V / 3A+ DC PSU. Red = +5V, black = GND.
- The HUB75 ribbon does NOT carry +5V. Panel power is fully separate from logic.
- ESP32: powered from Mac USB. 5V / 500 mA is plenty — the ESP32 only sources logic signals, no LED current.
- **Common ground is mandatory:** tie 5V PSU GND to ESP32 GND. Without it the HUB75 logic levels float and you get garbage.
- Add a 1000–2000 µF electrolytic capacitor across panel V+/GND near the panel input. Soaks up turn-on inrush; without it you get brownout flicker on bright frames.
- DO NOT power the panel from the ESP32's USB rail. Full white = ~3 A at 5 V. The ESP32 brownout protection trips and the panel glitches.

## 5. Potentiometer

10 kΩ linear, 3-pin.

| Pot terminal | Connection |
|---|---|
| 1 (one outer) | ESP32 GND |
| 2 (wiper, middle) | ESP32 **GPIO 33** (Feather silk: D33) |
| 3 (other outer) | ESP32 3V3 |

- GPIO 33 is ADC1 channel 5 — input-capable and ADC-capable. ADC2 pins conflict with Wi-Fi, so ADC1 is the right family for any analog read.
- Effect: 0Ω → mask is full white. Full Ω → mask is full red. Smooth EMA-filtered lerp in firmware (α = 0.1).
- If the direction feels backward, swap terminals 1 and 3.

## 6. Mode-toggle button

Tactile push-button, single-pole, normally open.

| Button terminal | Connection |
|---|---|
| One side | ESP32 **GPIO 34** (Feather silk: A2) |
| Other side | ESP32 GND |

- GPIO 34 is **input-only** and has **no internal pull-up**. An external pull-up to 3.3 V is **required** — a 10 kΩ resistor from GPIO 34 to 3V3 works. Without the pull-up the button reads as a floating bit and toggles on noise.
- Active-low: pressed = LOW, released = HIGH. The firmware debounces in software with a 50 ms window.
- On a confirmed press the firmware toggles its internal mode variable, emits a `0x10` (mask) or `0x11` (gloving) notification on the reverse channel, and clears the panel framebuffer.

## 7. Strapping pin caveat (GPIO 12 / G2)

- GPIO 12 is the ESP32's MTDI strapping pin. Its level at boot sets the internal flash voltage (1.8 V vs 3.3 V).
- Most Feather V2 boards boot fine with GPIO 12 wired to a HUB75 input — the panel input is high-impedance until the ESP32 drives it.
- **If your board fails to boot or fails to flash:** the `ESP32-HUB75-MatrixPanel-I2S-DMA` library lets you remap any signal. Move G2 from GPIO 12 to a free pin and update `Kyle/firmware/POT_working/src/main.cpp` accordingly.
- Watch the serial monitor on first boot. Brownouts or "invalid header" errors → that's GPIO 12.

## 8. Level shifting (optional but recommended)

- Panel logic is 5V; ESP32 outputs are 3.3V. Many HUB75 panels accept 3.3V at the controller IC, but it's panel-specific and refresh-rate-specific.
- **Symptoms of needing a level shifter:** flickering, ghost trails, wrong/dim colors on the first row, corrupted output above ~120 Hz refresh.
- **Fix:** insert a 74HCT245 (or 74AHCT245) between ESP32 outputs and HUB75 IN. VCC = 5V from the panel PSU. DIR tied high (always ESP→panel). One chip = 8 lines; covering all 13 signal lines wants either two 74HCT245s, or one 74HCT245 (R1, G1, B1, R2, G2, B2, CLK, LAT) plus a 74HCT125 quad buffer for A, B, C, D, E, OE.
- Easier alternative: an Adafruit "5V level shifter for HUB75" breakout. Drop-in.
- Skip if it works clean without one. It often does on this panel.

## 9. Cable + connector notes

- HUB75 ribbon: 16-pin IDC, 2×8, 0.1" pitch. Waveshare ships one. Keep <30 cm if running 3.3V direct; longer = level shifter mandatory.
- The panel has two HUB75 headers labeled **IN** and **OUT**. Drive into IN. OUT is for daisy-chaining additional panels; we are not chaining.
- VH4 power connector is keyed. Don't force it backward.

## 10. Bring-up checklist

1. Wire ESP32 GPIOs to HUB75 IN per §3.
2. Wire pot per §5.
3. Wire button per §6 (don't forget the external pull-up).
4. Connect 5V PSU to panel VH4 — panel OFF, supply OFF.
5. Tie ESP32 GND ↔ PSU GND.
6. Plug ESP32 into Mac.
7. Flash firmware: `pio run -t upload` from `Kyle/firmware/POT_working/`.
8. Power on the 5V PSU.
9. Run `python Kyle/vision/dashboard/Project_GUI.py` on the Mac (or the production aesthetics frontend once it lands).
10. Stand in front of the camera. Silhouette appears on panel. Turn pot — silhouette transitions white → red. Press the button — panel switches to glove mode.

> The ESP32's USB serial *is* the data link (1,000,000 baud). Do **not** open `pio device monitor` while the dashboard is running — both compete for `/dev/cu.usbserial-*`. To debug bring-up before running the host, monitor at 1000000 baud; the firmware doesn't print a banner — silence is normal.

## 11. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Panel dark, ESP32 boots fine | Panel PSU off, or VH4 not seated | check 5V supply |
| Panel garbled / first row bright, rest dim | 3.3V logic marginal | add level shifter (§8) |
| ESP32 reboots / brownout | Powering panel from ESP32, or no bulk cap | separate 5V PSU + 1000 µF cap |
| ESP32 won't flash | GPIO 12 strapping pin pulled high during boot | reroute G2 (§7) |
| Mask appears mirrored or rotated | Coord transform mismatch | swap x/y or invert in firmware loop |
| Silhouette there but no color change with pot | Pot on wrong GPIO, or wiper on wrong terminal | move pot wiper to GPIO 33, verify with serial monitor |
| Button toggles randomly / on its own | No external pull-up on GPIO 34 | add 10 kΩ from GPIO 34 to 3V3 |
| ACKs missing → Python prints retries | Serial baud mismatch, or `setRxBufferSize` not set | verify both sides at 1 Mbaud, RX buffer ≥ 2048 |

## 12. References

- Waveshare wiki: https://www.waveshare.com/wiki/RGB-Matrix-P2-64x64
- Library: https://github.com/mrcodetastic/ESP32-HUB75-MatrixPanel-DMA
- Adafruit ESP32 Feather V2 pinout: https://learn.adafruit.com/adafruit-esp32-feather-v2/pinouts
- HUB75 pinout reference: https://learn.adafruit.com/adafruit-triple-led-matrix-bonnet-for-raspberry-pi-with-hub75/pinouts
