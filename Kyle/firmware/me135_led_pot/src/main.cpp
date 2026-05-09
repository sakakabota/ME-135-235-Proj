// ME135 — ESP32 firmware for 64x64 HUB75 mask display + pot color control + finger-glove mode
// Target:  ESP32 DevKitC, Waveshare RGB-Matrix-P2 64x64 (HUB75E, 1/32 scan)
// Pin map (Waveshare HUB75 pin→GPIO): R1(16)=25 G1(15)=26 B1(14)=27 R2(12)=14 G2(11)=12 B2(10)=13  A(8)=23 B(7)=19 C(6)=5 D(5)=17 E(9)=32  LAT(3)=4 OE(2)=15 CLK(4)=16
// Pot:     GPIO34 (ADC1_CH6, input-only)
// Button:  GPIO33 (mode toggle, INPUT_PULLUP)
// Serial:  USB-CDC, 1,000,000 baud
// Frame:   [AA 55][LEN_H LEN_L][MODE][payload...][CRC_H CRC_L][55 AA]
//   Mode 0x00: binary mask (512B payload). Pot controls white→red lerp.
//   Mode 0x01: fingertip packet ([count][x,y,r,g,b]... up to 10 tips). Rendered as dots.
// Last modified: 2026-05-09

#include <Arduino.h>
#include <ESP32-HUB75-MatrixPanel-I2S-DMA.h>

// ---- Panel geometry ----
#define PANEL_WIDTH   64
#define PANEL_HEIGHT  64
#define PANEL_CHAIN   1
#define PAYLOAD_BYTES 512   // 64*64/8

// ---- Pin map (LOCKED) ----
#define R1_PIN  25
#define G1_PIN  26
#define B1_PIN  27
#define R2_PIN  14
#define G2_PIN  12
#define B2_PIN  13
#define A_PIN   23
#define B_PIN   19
#define C_PIN    5
#define D_PIN   17
#define E_PIN   32
#define LAT_PIN  4
#define OE_PIN  15
#define CLK_PIN 16

#define POT_PIN    34   // ADC1_CH6, input-only
#define BUTTON_PIN 33   // mode toggle, INPUT_PULLUP

// ---- Serial / protocol ----
#define SERIAL_BAUD       1000000
#define RX_BUFFER_SIZE    2048
#define FRAME_TIMEOUT_MS  100
#define WATCHDOG_MS       5000
#define ACK_BYTE  0x06
#define NAK_BYTE  0x15

#define MODE_MASK       0x00
#define MODE_FINGERTIPS 0x01
#define MODE_NOTIFY_0   0x10
#define MODE_NOTIFY_1   0x11

#define MAX_FINGERTIPS 10
#define DEBOUNCE_MS    50

// ---- Globals ----
MatrixPanel_I2S_DMA *dma_display = nullptr;

static uint8_t framebuf[PAYLOAD_BYTES] = {0};   // last-good mask payload (mode 0)
static uint8_t rxbuf[PAYLOAD_BYTES]    = {0};   // current frame in flight
static bool    fb_dirty = true;

static float    pot_ewma = -1.0f;
static float    last_t   = -1.0f;
static uint32_t lastFrameMs = 0;

static uint8_t  currentMode = MODE_MASK;
static uint8_t  rxModeByte  = 0;

// ---- Button ----
static bool     lastButtonState = HIGH;
static bool     debouncedButtonState = HIGH;
static uint32_t lastDebounceMs = 0;

// ---- Fingertip state (mode 1) ----
struct Fingertip {
    uint8_t x, y, r, g, b;
};
static Fingertip fingertips[MAX_FINGERTIPS];
static uint8_t   fingertipCount = 0;

// CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, no reflect, no xorout.
static uint16_t crc16_ccitt(const uint8_t *data, size_t len) {
    uint16_t crc = 0xFFFF;
    for (size_t i = 0; i < len; i++) {
        crc ^= (uint16_t)data[i] << 8;
        for (uint8_t j = 0; j < 8; j++) {
            if (crc & 0x8000) crc = (crc << 1) ^ 0x1021;
            else              crc <<= 1;
        }
    }
    return crc;
}

// ---- Receive state machine ----
enum RxState {
    RX_WAIT_AA,
    RX_WAIT_55,
    RX_LEN_HI,
    RX_LEN_LO,
    RX_MODE,
    RX_PAYLOAD,
    RX_CRC_HI,
    RX_CRC_LO,
    RX_END_55,
    RX_END_AA
};

enum RxResult { RX_NONE, RX_OK, RX_CRC_ERROR, RX_SYNC_ERROR };

static RxState  rxState   = RX_WAIT_AA;
static uint16_t rxLen     = 0;
static uint16_t rxIdx     = 0;
static uint16_t rxCrc     = 0;
static uint32_t rxStartMs = 0;

static void resetRx() {
    rxState = RX_WAIT_AA;
    rxIdx   = 0;
}

static RxResult pollFrame() {
    if (rxState > RX_WAIT_55 && (millis() - rxStartMs) > FRAME_TIMEOUT_MS) {
        resetRx();
        return RX_SYNC_ERROR;
    }

    while (Serial.available()) {
        uint8_t b = (uint8_t)Serial.read();

        switch (rxState) {
        case RX_WAIT_AA:
            if (b == 0xAA) { rxState = RX_WAIT_55; rxStartMs = millis(); }
            break;

        case RX_WAIT_55:
            if (b == 0x55)      rxState = RX_LEN_HI;
            else if (b == 0xAA) rxStartMs = millis();
            else                rxState = RX_WAIT_AA;
            break;

        case RX_LEN_HI:
            rxLen = (uint16_t)b << 8;
            rxState = RX_LEN_LO;
            break;

        case RX_LEN_LO:
            rxLen |= b;
            rxState = RX_MODE;
            break;

        case RX_MODE:
            rxModeByte = b;
            if (rxModeByte == MODE_MASK) {
                if (rxLen != PAYLOAD_BYTES) { resetRx(); return RX_SYNC_ERROR; }
            } else if (rxModeByte == MODE_FINGERTIPS) {
                if (rxLen > (1 + MAX_FINGERTIPS * 5)) { resetRx(); return RX_SYNC_ERROR; }
            } else {
                resetRx();
                return RX_SYNC_ERROR;
            }
            rxIdx = 0;
            rxState = (rxLen > 0) ? RX_PAYLOAD : RX_CRC_HI;
            break;

        case RX_PAYLOAD:
            if (rxIdx < PAYLOAD_BYTES) rxbuf[rxIdx] = b;
            rxIdx++;
            if (rxIdx >= rxLen) rxState = RX_CRC_HI;
            break;

        case RX_CRC_HI:
            rxCrc = (uint16_t)b << 8;
            rxState = RX_CRC_LO;
            break;

        case RX_CRC_LO:
            rxCrc |= b;
            rxState = RX_END_55;
            break;

        case RX_END_55:
            if (b != 0x55) { resetRx(); return RX_SYNC_ERROR; }
            rxState = RX_END_AA;
            break;

        case RX_END_AA:
            if (b != 0xAA) { resetRx(); return RX_SYNC_ERROR; }
            // CRC over MODE byte + payload
            {
                uint8_t crcBuf[1 + PAYLOAD_BYTES];
                crcBuf[0] = rxModeByte;
                if (rxLen > 0) memcpy(crcBuf + 1, rxbuf, rxLen);
                uint16_t calc = crc16_ccitt(crcBuf, 1 + rxLen);
                resetRx();
                if (calc != rxCrc) return RX_CRC_ERROR;
                if (rxModeByte != currentMode) return RX_SYNC_ERROR;
                if (rxModeByte == MODE_MASK) {
                    memcpy(framebuf, rxbuf, PAYLOAD_BYTES);
                } else if (rxModeByte == MODE_FINGERTIPS) {
                    fingertipCount = (rxLen > 0) ? rxbuf[0] : 0;
                    if (fingertipCount > MAX_FINGERTIPS) fingertipCount = MAX_FINGERTIPS;
                    if (rxLen < (uint16_t)(1 + fingertipCount * 5)) {
                        return RX_SYNC_ERROR;
                    }
                    for (int i = 0; i < fingertipCount; i++) {
                        int off = 1 + i * 5;
                        fingertips[i].x = rxbuf[off];
                        fingertips[i].y = rxbuf[off + 1];
                        fingertips[i].r = rxbuf[off + 2];
                        fingertips[i].g = rxbuf[off + 3];
                        fingertips[i].b = rxbuf[off + 4];
                    }
                }
                return RX_OK;
            }
        }
    }
    return RX_NONE;
}

// ---- Render ----
static void renderMask() {
    for (int i = 0; i < PANEL_WIDTH * PANEL_HEIGHT; i++) {
        uint8_t byte = framebuf[i >> 3];
        uint8_t bit  = (byte >> (7 - (i & 7))) & 0x01;
        int x = i % PANEL_WIDTH;
        int y = i / PANEL_WIDTH;
        if (bit) {
            float t = (last_t > 0.0f) ? last_t : 0.0f;
            uint8_t cr = 255;
            uint8_t cg = (uint8_t)((1.0f - t) * 255.0f);
            uint8_t cb = (uint8_t)((1.0f - t) * 255.0f);
            dma_display->drawPixelRGB888(x, y, cr, cg, cb);
        } else {
            dma_display->drawPixelRGB888(x, y, 0, 0, 0);
        }
    }
}

static void renderFingertips() {
    // Draw each fingertip as a 3x3 block for visibility at 2mm pitch
    for (int i = 0; i < fingertipCount; i++) {
        Fingertip ft = fingertips[i];
        for (int dy = -1; dy <= 1; dy++) {
            for (int dx = -1; dx <= 1; dx++) {
                int px = ft.x + dx;
                int py = ft.y + dy;
                if (px >= 0 && px < PANEL_WIDTH && py >= 0 && py < PANEL_HEIGHT) {
                    dma_display->drawPixelRGB888(px, py, ft.r, ft.g, ft.b);
                }
            }
        }
    }
}

static void blankPanel() {
    dma_display->fillScreenRGB888(0, 0, 0);
}

// ---- Setup ----
void setup() {
    Serial.setRxBufferSize(RX_BUFFER_SIZE);
    Serial.begin(SERIAL_BAUD);

    analogReadResolution(12);
    analogSetPinAttenuation(POT_PIN, ADC_11db);

    pinMode(BUTTON_PIN, INPUT_PULLUP);

    HUB75_I2S_CFG::i2s_pins pins = {
        R1_PIN, G1_PIN, B1_PIN,
        R2_PIN, G2_PIN, B2_PIN,
        A_PIN,  B_PIN,  C_PIN, D_PIN, E_PIN,
        LAT_PIN, OE_PIN, CLK_PIN
    };
    HUB75_I2S_CFG mxconfig(PANEL_WIDTH, PANEL_HEIGHT, PANEL_CHAIN, pins);
    mxconfig.clkphase = false;

    dma_display = new MatrixPanel_I2S_DMA(mxconfig);
    dma_display->begin();
    dma_display->setBrightness8(160);
    blankPanel();

    lastFrameMs = millis();
}

// ---- Loop ----
void loop() {
    // 1. Button — debounced mode toggle
    bool reading = digitalRead(BUTTON_PIN);
    if (reading != lastButtonState) {
        lastDebounceMs = millis();
    }
    if ((millis() - lastDebounceMs) > DEBOUNCE_MS) {
        if (reading != debouncedButtonState) {
            bool previousDebouncedState = debouncedButtonState;
            debouncedButtonState = reading;
            if (debouncedButtonState == LOW && previousDebouncedState == HIGH) {  // pressed (pull-up)
                currentMode = (currentMode == MODE_MASK) ? MODE_FINGERTIPS : MODE_MASK;
                fb_dirty = true;
                Serial.write(currentMode == MODE_MASK ? MODE_NOTIFY_0 : MODE_NOTIFY_1);
            }
        }
    }
    lastButtonState = reading;

    // 2. RX
    RxResult r = pollFrame();
    if (r == RX_OK) {
        Serial.write(ACK_BYTE);
        fb_dirty    = true;
        lastFrameMs = millis();
    } else if (r == RX_CRC_ERROR || r == RX_SYNC_ERROR) {
        Serial.write(NAK_BYTE);
    }

    // 3. Pot — EWMA smoothing (mode 0 only)
    int raw = analogRead(POT_PIN);
    if (pot_ewma < 0.0f) pot_ewma = (float)raw;
    pot_ewma = 0.1f * (float)raw + 0.9f * pot_ewma;
    float t = pot_ewma / 4095.0f;
    if (t < 0.0f) t = 0.0f; else if (t > 1.0f) t = 1.0f;

    // 4. Render
    bool t_changed = (last_t < 0.0f) || (fabsf(t - last_t) >= 0.01f);
    if (fb_dirty || (currentMode == MODE_MASK && t_changed)) {
        blankPanel();
        if (currentMode == MODE_MASK) {
            renderMask();
        } else {
            renderFingertips();
        }
        fb_dirty = false;
        last_t   = t;
    }

    // 5. Watchdog — blank if no frames received (mode 0 only; mode 1 stays on)
    if (currentMode == MODE_MASK && (millis() - lastFrameMs) > WATCHDOG_MS) {
        memset(framebuf, 0, PAYLOAD_BYTES);
        blankPanel();
        lastFrameMs = millis();
        last_t      = -1.0f;
    }
}
