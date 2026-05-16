# me135_led_pot

ESP32 firmware for the ME135 vision pipeline endpoint. Receives 64x64 binary
mask frames from the Mac over USB-CDC at 1 Mbaud, displays them on a Waveshare
RGB-Matrix-P2 64x64 HUB75E panel, and uses a 10k potentiometer on GPIO34 to
lerp the foreground color from white (pot=0) to red (pot=full).

## Build

```
pio run
```

## Flash

```
pio run -t upload
```

## Monitor

```
pio device monitor
```

Monitor baud is 115200 for any future debug prints. The data link to the Mac
runs over the same USB-CDC at 1,000,000 baud, so close the monitor before
running the Mac sender.

## Pin reference (Adafruit ESP32 Feather V2)

Board: [Adafruit ESP32 Feather V2](https://learn.adafruit.com/adafruit-esp32-feather-v2/pinouts).
Three GPIOs available on a generic ESP32 DevKitC are unavailable on the Feather V2: `16` and
`17` are reserved for the on-module PSRAM, and `23` is not broken out at all. `A`, `D`, and
`CLK` were remapped accordingly.

| HUB75 | GPIO | Feather V2 silk |
|------:|:----:|:----------------|
| R1    | 25   | A1   |
| G1    | 26   | A0   |
| B1    | 27   | 27   |
| R2    | 14   | 14   |
| G2    | 12   | 12 — strapping pin (flash-voltage select); never pull high before reset |
| B2    | 13   | 13 — also drives the onboard red LED (cosmetic) |
| A     | 21   | MI   |
| B     | 19   | MO   |
| C     | 5    | SCK  |
| D     | 20   | SCL  |
| E     | 32   | 32   |
| LAT   | 4    | A5   |
| OE    | 15   | 15   |
| CLK   | 22   | SDA  |
| POT   | 33   | 33 (ADC1_CH5)                                            |
| BTN   | 34   | A2 (input-only, **needs external 10k pull-up to 3.3 V**) |

## Power

Power the panel from a separate 5V / 3A+ supply. Do NOT power the panel from
the ESP32's 5V rail — a fully lit 64x64 will pull several amps and brown out
the dev board. Tie the panel ground to the ESP32 ground.

## Wire protocol

```
[0xAA][0x55][0x02][0x00][512 payload][CRC_H][CRC_L][0x55][0xAA]   = 520 bytes
```

CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF) over the 512-byte payload only.
ESP32 replies 0x06 on success, 0x15 on CRC or sync error. If no valid frame
arrives for 5 seconds, the panel blanks until the Mac resumes.
