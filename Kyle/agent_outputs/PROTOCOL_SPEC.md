# ME135 Serial Protocol Specification
## Jetson ↔ ESP32 Binary Matrix Transport

| Field        | Description                              |
|------------- |------------------------------------------|
| **Version**  | 1.0                                      |
| **Date**     | Spring 2026                              |
| **Authors**  | ME135 Team — UC Berkeley                 |

---

## 1. Overview

The Jetson (host) transmits a **400 × 300 binary pixel matrix** to the ESP32
(display controller) over **UART** at **2 Mbaud**. Each frame is bit-packed
into **15,000 bytes** and wrapped in a lightweight framing protocol with
CRC-16 error detection and ACK/NAK flow control.

## 2. Physical Layer

| Parameter   | Value                      |
|------------ |----------------------------|
| Interface   | UART (3.3 V logic)         |
| Baud rate   | 2,000,000                  |
| Data bits   | 8                          |
| Parity      | None                       |
| Stop bits   | 1                          |
| Flow ctrl   | None (software ACK/NAK)    |

**Wiring (Jetson → ESP32):**
```
Jetson TX  →  ESP32 RX (GPIO 16)
Jetson RX  ←  ESP32 TX (GPIO 17)
Jetson GND —  ESP32 GND
```

> ⚠️ Both Jetson and ESP32 are 3.3 V logic — **no level shifter needed**.

## 3. Frame Format

```
Byte offset   Field          Size     Value / Encoding
──────────────────────────────────────────────────────
0             START[0]       1        0xAA
1             START[1]       1        0x55
2-3           PAYLOAD_LEN    2        Big-endian uint16 (= 15000 = 0x3A98)
4-15003       PAYLOAD        15000    Bit-packed matrix (see §4)
15004-15005   CRC-16         2        Big-endian CRC-16/CCITT-FALSE over PAYLOAD
15006         END[0]         1        0x55
15007         END[1]         1        0xAA
──────────────────────────────────────────────────────
Total                        15008 bytes
```

## 4. Payload Encoding

The 400 × 300 binary matrix is serialised **row-major, MSB-first**:

1. Flatten the matrix row-by-row: pixel (0,0), (0,1), …, (0,399), (1,0), …
2. Pack every 8 consecutive pixels into one byte, MSB = first pixel.
3. Total: 120,000 bits → 15,000 bytes.

Pixel value: `0` = background, `1` = human detected.

## 5. CRC-16 / CCITT-FALSE

| Parameter   | Value          |
|------------ |----------------|
| Polynomial  | 0x1021         |
| Init        | 0xFFFF         |
| RefIn       | false          |
| RefOut      | false          |
| XorOut      | 0x0000         |

CRC is computed **over the PAYLOAD bytes only** (offsets 4–15003).

## 6. Flow Control

After receiving a complete frame, the ESP32 responds with a **single byte**:

| Response | Byte | Meaning                                  |
|--------- |----- |------------------------------------------|
| **ACK**  | 0x06 | Frame received, CRC valid, displaying    |
| **NAK**  | 0x15 | CRC mismatch or frame error, resend      |

The Jetson waits up to **50 ms** for a response. On NAK or timeout, it
retransmits the same frame (up to 3 attempts).

## 7. Timing Budget

```
Frame size:  15,008 bytes
Baud rate:   2,000,000 bps  → ~200,000 bytes/s
TX time:     ~75 ms / frame
ACK window:  50 ms (worst case)
──────────────────────────
Theoretical: ~8 fps sustained
Target:      ≥ 10 fps (pipeline parallelism hides TX latency)
```

## 8. Error Handling

| Condition              | Jetson Action            | ESP32 Action         |
|----------------------- |--------------------------|----------------------|
| CRC mismatch           | Retransmit (up to 3×)    | Send NAK (0x15)      |
| ACK timeout            | Retransmit               | —                    |
| 3 consecutive failures | Log error, skip frame    | —                    |
| 10+ serial errors      | Safety shutdown           | Watchdog reset       |
| Invalid start marker   | —                        | Discard, re-sync     |

## 9. ESP32 Response Extensions (Future)

| Byte | Meaning                    |
|----- |----------------------------|
| 0x06 | ACK                        |
| 0x15 | NAK                        |
| 0x07 | HEARTBEAT (→ LabVIEW hub)  |
| 0x10 | STATUS_REQUEST             |

---

*End of Protocol Specification v1.0*
