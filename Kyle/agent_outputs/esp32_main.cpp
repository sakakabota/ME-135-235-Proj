/**
 * ME135 Human Detection — ESP32 Display Controller Firmware
 * ==========================================================
 * Receives a bit-packed 108×108 binary matrix from the Jetson via UART,
 * verifies CRC-16, and drives a WS2812B LED panel (108×108 = 11,664 LEDs).
 *
 * Note: CV processing runs at 400×300 on the Jetson; the matrix is
 * downsampled to 108×108 before transmission to match the LED panel.
 *
 * Protocol: see PROTOCOL_SPEC.md
 *
 * Pin assignments (match config.yaml and PROTOCOL_SPEC.md):
 *   UART RX  = GPIO 16
 *   UART TX  = GPIO 17
 *   LED Data = GPIO 13
 *
 * Build with PlatformIO:  pio run -t upload
 */

#include <Arduino.h>
#include <Adafruit_NeoPixel.h>

// ============================================================
// Configuration — must match config.yaml and PROTOCOL_SPEC.md
// ============================================================
#define SERIAL_BAUD       2000000
#define UART_RX_PIN       16
#define UART_TX_PIN       17

#define MATRIX_COLS       108    // Physical LED panel columns
#define MATRIX_ROWS       108    // Physical LED panel rows (108x108 = 11,664 LEDs)
#define PAYLOAD_BYTES     1458   // (108 * 108) / 8

#define FRAME_HEADER_SIZE 4      // 0xAA 0x55 LEN_H LEN_L
#define FRAME_FOOTER_SIZE 4      // CRC_H CRC_L 0x55 0xAA
#define FRAME_TOTAL_SIZE  (FRAME_HEADER_SIZE + PAYLOAD_BYTES + FRAME_FOOTER_SIZE)

#define START_BYTE_0      0xAA
#define START_BYTE_1      0x55
#define END_BYTE_0        0x55
#define END_BYTE_1        0xAA

#define ACK_BYTE          0x06
#define NAK_BYTE          0x15

#define LED_PIN           13
#define LED_COUNT         11664   // 108 * 108
#define LED_BRIGHTNESS    128

#define WATCHDOG_TIMEOUT_MS  5000  // Safety: reset if no frame in 5 s

// ============================================================
// Globals
// ============================================================
static uint8_t rxBuffer[FRAME_TOTAL_SIZE];
static uint8_t payload[PAYLOAD_BYTES];
static volatile uint32_t lastFrameMs = 0;

// Use HardwareSerial1 for Jetson comms (pins 16/17)
HardwareSerial JetsonSerial(1);

// NeoPixel strip — adjust pixel count to your actual panel
Adafruit_NeoPixel strip(LED_COUNT, LED_PIN, NEO_GRB + NEO_KHZ800);

// ============================================================
// CRC-16 / CCITT-FALSE  (must match serial_protocol.py)
// ============================================================
uint16_t crc16_ccitt(const uint8_t *data, size_t len) {
    uint16_t crc = 0xFFFF;
    for (size_t i = 0; i < len; i++) {
        crc ^= (uint16_t)data[i] << 8;
        for (uint8_t j = 0; j < 8; j++) {
            if (crc & 0x8000)
                crc = (crc << 1) ^ 0x1021;
            else
                crc <<= 1;
        }
    }
    return crc;
}

// ============================================================
// Frame receiver — blocking with timeout
// ============================================================
enum RxResult { RX_OK, RX_TIMEOUT, RX_CRC_ERROR, RX_SYNC_ERROR };

/**
 * Wait for a complete frame. Returns status.
 * On RX_OK, `payload` buffer contains the 1,458-byte payload.
 */
RxResult receiveFrame() {
    // --- Wait for start marker ---
    uint32_t t0 = millis();
    int state = 0;  // 0 = waiting for 0xAA, 1 = waiting for 0x55
    while (state < 2) {
        if (millis() - t0 > WATCHDOG_TIMEOUT_MS) return RX_TIMEOUT;
        if (JetsonSerial.available()) {
            uint8_t b = JetsonSerial.read();
            if (state == 0 && b == START_BYTE_0) state = 1;
            else if (state == 1 && b == START_BYTE_1) state = 2;
            else state = 0;  // re-sync
        }
    }

    // --- Read length (2 bytes, big-endian) ---
    while (JetsonSerial.available() < 2) {
        if (millis() - t0 > WATCHDOG_TIMEOUT_MS) return RX_TIMEOUT;
    }
    uint8_t lenH = JetsonSerial.read();
    uint8_t lenL = JetsonSerial.read();
    uint16_t payloadLen = ((uint16_t)lenH << 8) | lenL;

    if (payloadLen != PAYLOAD_BYTES) {
        return RX_SYNC_ERROR;
    }

    // --- Read payload ---
    size_t received = 0;
    while (received < PAYLOAD_BYTES) {
        if (millis() - t0 > WATCHDOG_TIMEOUT_MS) return RX_TIMEOUT;
        size_t avail = JetsonSerial.available();
        if (avail > 0) {
            size_t toRead = min(avail, (size_t)(PAYLOAD_BYTES - received));
            JetsonSerial.readBytes(&payload[received], toRead);
            received += toRead;
        }
    }

    // --- Read CRC (2 bytes) + end marker (2 bytes) ---
    uint8_t tail[4];
    size_t tailRx = 0;
    while (tailRx < 4) {
        if (millis() - t0 > WATCHDOG_TIMEOUT_MS) return RX_TIMEOUT;
        if (JetsonSerial.available()) {
            tail[tailRx++] = JetsonSerial.read();
        }
    }

    uint16_t rxCrc = ((uint16_t)tail[0] << 8) | tail[1];
    if (tail[2] != END_BYTE_0 || tail[3] != END_BYTE_1) {
        return RX_SYNC_ERROR;
    }

    // --- Verify CRC ---
    uint16_t calcCrc = crc16_ccitt(payload, PAYLOAD_BYTES);
    if (calcCrc != rxCrc) {
        return RX_CRC_ERROR;
    }

    return RX_OK;
}

// ============================================================
// Display driver — push payload bits to LED panel
// ============================================================
void updateDisplay() {
    /*
     * Map bit-packed 108×108 payload to NeoPixels.
     * Pixel (row, col) → bit index = row * MATRIX_COLS + col
     * Byte index = bit_index / 8
     * Bit position = 7 - (bit_index % 8)   [MSB-first]
     */
    uint32_t maxPixels = LED_COUNT;  // 11,664 — exactly matches 108*108
    for (uint32_t i = 0; i < maxPixels; i++) {
        uint8_t byteVal = payload[i >> 3];          // i / 8
        uint8_t bit = (byteVal >> (7 - (i & 7))) & 1;  // MSB-first
        if (bit) {
            strip.setPixelColor(i, strip.Color(255, 255, 255));  // Human = white
        } else {
            strip.setPixelColor(i, strip.Color(0, 0, 0));        // Background = off
        }
    }
    strip.show();
}

// ============================================================
// Setup
// ============================================================
void setup() {
    // Debug serial (USB)
    Serial.begin(115200);
    Serial.println("[ME135] ESP32 Display Controller starting…");

    // Jetson UART
    // RX buffer — 1,466 bytes/frame; 4KB is plenty
    // Must be called BEFORE begin() — after begin() it is a no-op
    JetsonSerial.setRxBufferSize(4096);
    JetsonSerial.begin(SERIAL_BAUD, SERIAL_8N1, UART_RX_PIN, UART_TX_PIN);

    // LED panel
    strip.begin();
    strip.setBrightness(LED_BRIGHTNESS);
    strip.clear();
    strip.show();

    lastFrameMs = millis();
    Serial.println("[ME135] Ready — waiting for frames from Jetson.");
}

// ============================================================
// Main loop
// ============================================================
void loop() {
    static bool skipDisplay = false;
    RxResult result = receiveFrame();

    switch (result) {
        case RX_OK:
            JetsonSerial.write(ACK_BYTE);
            lastFrameMs = millis();
            if (!skipDisplay) {
                updateDisplay();
            }
            // If data is already queued, skip next display update to catch up
            skipDisplay = (JetsonSerial.available() >= FRAME_HEADER_SIZE);
            break;

        case RX_CRC_ERROR:
            Serial.println("[ME135] CRC error — sending NAK");
            JetsonSerial.write(NAK_BYTE);
            break;

        case RX_SYNC_ERROR:
            Serial.println("[ME135] Sync error — sending NAK");
            JetsonSerial.write(NAK_BYTE);
            break;

        case RX_TIMEOUT:
            // Watchdog: no frame received within timeout window
            if (millis() - lastFrameMs > WATCHDOG_TIMEOUT_MS) {
                Serial.println("[ME135] WATCHDOG — no frame, blanking display");
                strip.clear();
                strip.show();
                lastFrameMs = millis();  // reset to avoid spam
            }
            break;
    }
}
