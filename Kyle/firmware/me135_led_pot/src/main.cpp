// ME135 — ESP32 firmware for 64x64 HUB75 mask display + pot color control
// Target:  ESP32 DevKitC, Waveshare RGB-Matrix-P2 64x64 (HUB75E, 1/32 scan)
// Pin map: R1=25 G1=26 B1=27 R2=14 G2=12 B2=13  A=23 B=19 C=5 D=17 E=32  LAT=4 OE=15 CLK=16
// Pot:     GPIO34 (ADC1_CH6, input-only)
// Serial:  USB-CDC, 1,000,000 baud
// Frame:   [AA 55][02 00][512 payload bits row-major MSB-first][CRC16-H][CRC16-L][55 AA] = 520 B
// Last modified: 2026-05-07

#include <Arduino.h>
#include <ESP32-HUB75-MatrixPanel-I2S-DMA.h>

// ---- Panel geometry ----
#define PANEL_WIDTH   64
#define PANEL_HEIGHT  64
#define PANEL_CHAIN   1
#define PAYLOAD_BYTES 512   // 64*64/8

// Color depth: leave at lib default (8) for now. If RAM gets tight (this panel
// at 8 bpp is fine on vanilla ESP32), set to 6 and call setPixelColorDepthBits.
#define COLOR_DEPTH 8

// ---- Pin map (LOCKED) ----
#define R1_PIN  25
#define G1_PIN  26
#define B1_PIN  27
#define R2_PIN  14
#define G2_PIN  12   // GPIO12 is a strapping pin (MTDI). If board fails to boot,
                     // ensure no strong external pull at reset; reflash via UART if stuck.
#define B2_PIN  13
#define A_PIN   23
#define B_PIN   19
#define C_PIN    5
#define D_PIN   17
#define E_PIN   32   // required for 1/32-scan 64-row panels
#define LAT_PIN  4
#define OE_PIN  15
#define CLK_PIN 16

#define POT_PIN 34   // ADC1_CH6, input-only

// ---- Serial / protocol ----
#define SERIAL_BAUD       1000000
#define RX_BUFFER_SIZE    2048
#define FRAME_TIMEOUT_MS  100
#define WATCHDOG_MS       5000
#define ACK_BYTE  0x06
#define NAK_BYTE  0x15

// ---- Globals ----
MatrixPanel_I2S_DMA *dma_display = nullptr;

static uint8_t framebuf[PAYLOAD_BYTES] = {0};   // last-good mask payload
static uint8_t rxbuf[PAYLOAD_BYTES]    = {0};   // current frame in flight
static bool    fb_dirty = true;                 // force first redraw

static float    pot_ewma = -1.0f;               // -1 = uninitialized
static float    last_t   = -1.0f;
static uint32_t lastFrameMs = 0;

// CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, no reflect, no xorout.
// Matches Kyle/agent_outputs/esp32_main.cpp lines 80-92 and serial_protocol.py.
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

// Pull bytes; return RX_OK on a complete valid frame, error codes on failure,
// RX_NONE if still in progress / idle.
static RxResult pollFrame() {
    // Per-frame timeout: only enforced once we've started parsing past the start markers.
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
            else if (b == 0xAA) rxStartMs = millis();   // stay armed on AA AA
            else                rxState = RX_WAIT_AA;
            break;

        case RX_LEN_HI:
            rxLen = (uint16_t)b << 8;
            rxState = RX_LEN_LO;
            break;

        case RX_LEN_LO:
            rxLen |= b;
            // Mac sends [0x02][0x00] = 0x0200 = 512 (big-endian)
            if (rxLen != PAYLOAD_BYTES) { resetRx(); return RX_SYNC_ERROR; }
            rxIdx = 0;
            rxState = RX_PAYLOAD;
            break;

        case RX_PAYLOAD:
            rxbuf[rxIdx++] = b;
            if (rxIdx >= PAYLOAD_BYTES) rxState = RX_CRC_HI;
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
            // Full frame in. Validate CRC over payload only.
            {
                uint16_t calc = crc16_ccitt(rxbuf, PAYLOAD_BYTES);
                resetRx();
                if (calc != rxCrc) return RX_CRC_ERROR;
                memcpy(framebuf, rxbuf, PAYLOAD_BYTES);
                return RX_OK;
            }
        }
    }
    return RX_NONE;
}

// ---- Render ----
static void renderFrame(uint8_t r, uint8_t g, uint8_t b) {
    // Bit-unpack: 4096 pixels, MSB-first per byte, row-major.
    for (int i = 0; i < PANEL_WIDTH * PANEL_HEIGHT; i++) {
        uint8_t byte = framebuf[i >> 3];
        uint8_t bit  = (byte >> (7 - (i & 7))) & 0x01;
        int x = i % PANEL_WIDTH;
        int y = i / PANEL_WIDTH;
        if (bit) dma_display->drawPixelRGB888(x, y, r, g, b);
        else     dma_display->drawPixelRGB888(x, y, 0, 0, 0);
    }
}

static void blankPanel() {
    dma_display->fillScreenRGB888(0, 0, 0);
}

// ---- Setup ----
void setup() {
    // setRxBufferSize must be called BEFORE begin — after begin it's a no-op.
    Serial.setRxBufferSize(RX_BUFFER_SIZE);
    Serial.begin(SERIAL_BAUD);

    analogReadResolution(12);
    analogSetPinAttenuation(POT_PIN, ADC_11db);

    HUB75_I2S_CFG::i2s_pins pins = {
        R1_PIN, G1_PIN, B1_PIN,
        R2_PIN, G2_PIN, B2_PIN,
        A_PIN,  B_PIN,  C_PIN, D_PIN, E_PIN,
        LAT_PIN, OE_PIN, CLK_PIN
    };
    HUB75_I2S_CFG mxconfig(PANEL_WIDTH, PANEL_HEIGHT, PANEL_CHAIN, pins);
    mxconfig.clkphase = false;
    // mxconfig.setPixelColorDepthBits(COLOR_DEPTH);  // enable if RAM tight

    dma_display = new MatrixPanel_I2S_DMA(mxconfig);
    dma_display->begin();
    dma_display->setBrightness8(160);
    blankPanel();

    lastFrameMs = millis();
}

// ---- Loop ----
void loop() {
    // 1. RX
    RxResult r = pollFrame();
    if (r == RX_OK) {
        Serial.write(ACK_BYTE);
        fb_dirty    = true;
        lastFrameMs = millis();
    } else if (r == RX_CRC_ERROR || r == RX_SYNC_ERROR) {
        Serial.write(NAK_BYTE);
    }

    // 2. Pot — EWMA smoothing, normalized to [0,1].
    int raw = analogRead(POT_PIN);
    if (pot_ewma < 0.0f) pot_ewma = (float)raw;
    pot_ewma = 0.1f * (float)raw + 0.9f * pot_ewma;
    float t = pot_ewma / 4095.0f;
    if (t < 0.0f) t = 0.0f; else if (t > 1.0f) t = 1.0f;

    // 3. Color lerp: t=0 white, t=1 red.
    uint8_t cr = 255;
    uint8_t cg = (uint8_t)((1.0f - t) * 255.0f);
    uint8_t cb = (uint8_t)((1.0f - t) * 255.0f);

    // 4. Redraw only when something actually changed.
    bool t_changed = (last_t < 0.0f) || (fabsf(t - last_t) >= 0.01f);
    if (fb_dirty || t_changed) {
        renderFrame(cr, cg, cb);
        fb_dirty = false;
        last_t   = t;
    }

    // 5. Watchdog — blank if Mac stops sending.
    if ((millis() - lastFrameMs) > WATCHDOG_MS) {
        memset(framebuf, 0, PAYLOAD_BYTES);
        blankPanel();
        lastFrameMs = millis();   // avoid re-blanking every loop
        last_t      = -1.0f;      // force redraw when frames resume
    }
}
