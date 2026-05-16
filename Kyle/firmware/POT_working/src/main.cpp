// ME135 ESP32 firmware 64x64 HUB75 mask display pot color finger glove mode
// Target Adafruit ESP32 Feather V2 Waveshare RGB Matrix P2 64x64 HUB75E 
//
// Pin map HUB75 pin Feather V2 silk GPIO 
// R1 16 A1 25 G1 15 A0 26 B1 14 27 R2 12 14 G2 11 12 B2 10 13
// A 8 MI 21 B 7 MO 19 C 6 SCK 5 D 5 SCL 20 E 9 32 LAT 3 A5 4
// OE 2 15 CLK 4 SDA 22
// Feather V2 gotchas 
// GPIO 16 17 23 not broken out 16 17 used by PSRAM 23 not exposed so
// A D CLK got remapped to MI SCL SDA 
// GPIO 12 G2 is the flash boot strap pin HUB75 cable only receives so safe 
// but don t ever pull G2 high externally before reset 
// GPIO 13 B2 shares the onboard red LED LED will blink with B2 cosmetic 
// Pot GPIO33 ADC1_CH5 
// Button GPIO34 input only needs EXTERNAL 3 3V pull up No internal pull up exists 
// Serial USB CDC 1 Mbaud 
// Frame AA 55 LEN_H LEN_L MODE payload CRC_H CRC_L 55 AA 
// mode 0x00 512B mask Pot lerps white red 
// mode 0x01 fingertips count x y r g b 10max Renders as dots 

#include <Arduino.h>
#include <ESP32-HUB75-MatrixPanel-I2S-DMA.h>

// panel geometry
#define PANEL_WIDTH   64
#define PANEL_HEIGHT  64
#define PANEL_CHAIN   1
#define PAYLOAD_BYTES 512   // 64 64 8

// pin map DO NOT change without re checking Feather V2 pinout
#define R1_PIN  25
#define G1_PIN  26
#define B1_PIN  27
#define R2_PIN  14
#define G2_PIN  12
#define B2_PIN  13
#define A_PIN   21   // Feather V2 silk MI was 23 on DevKitC 23 not broken out on V2 
#define B_PIN   19   // Feather V2 silk MO
#define C_PIN    5   // Feather V2 silk SCK
#define D_PIN   20   // Feather V2 silk SCL was 17 on DevKitC 17 used by PSRAM on V2 
#define E_PIN   32
#define LAT_PIN  4   // Feather V2 silk A5
#define OE_PIN  15
#define CLK_PIN 22   // Feather V2 silk SDA was 16 on DevKitC 16 used by PSRAM on V2 

#define POT_PIN    33   // ADC1_CH5
#define BUTTON_PIN 34   // input only needs EXTERNAL pull up to 3 3V no internal pull up on GPIO 34 

// serial protocol
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
#define POT_NOTIFY      0x20   // followed by 1 byte pot 0 255 

#define MAX_FINGERTIPS 10
#define DEBOUNCE_MS    200

// globals
MatrixPanel_I2S_DMA *dma_display = nullptr;

static uint8_t framebuf[PAYLOAD_BYTES] = {0};   // last good mask payload mode 0 
static uint8_t rxbuf[PAYLOAD_BYTES]    = {0};   // current frame in flight
static bool    fb_dirty = true;

static float    pot_ewma = -1.0f;
static float    last_t   = -1.0f;
static uint32_t lastFrameMs = 0;
static int      lastReportedPot = -1;
static uint32_t lastPotReportMs = 0;

static uint8_t  currentMode = MODE_MASK;
static uint8_t  rxModeByte  = 0;

// button debounce state
static bool     lastButtonState = HIGH;
static bool     debouncedButtonState = HIGH;
static uint32_t lastDebounceMs = 0;

// fingertip state for mode 1
struct Fingertip {
    uint8_t x, y, r, g, b;
};
static Fingertip fingertips[MAX_FINGERTIPS];
static uint8_t   fingertipCount = 0;

// CRC 16 CCITT FALSE poly 0x1021 init 0xFFFF no reflect no xorout 
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

// rx state machine
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
            // CRC over MODE byte payload
            {
                uint8_t crcBuf[1 + PAYLOAD_BYTES];
                crcBuf[0] = rxModeByte;
                if (rxLen > 0) memcpy(crcBuf + 1, rxbuf, rxLen);
                uint16_t calc = crc16_ccitt(crcBuf, 1 + rxLen);
                resetRx();
                if (calc != rxCrc) return RX_CRC_ERROR;
                
                // Adopt the host s mode instead of throwing a NAK loop 
                if (rxModeByte != currentMode) {
                    currentMode = rxModeByte;
                    fb_dirty = true;
                }
                
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

// takes t directly do NOT read last_t it updates after render and would be stale
static void renderMask(float t) {
    if (t < 0.0f) t = 0.0f; else if (t > 1.0f) t = 1.0f;
    uint8_t cr = 255;
    uint8_t cg = (uint8_t)((1.0f - t) * 255.0f);
    uint8_t cb = (uint8_t)((1.0f - t) * 255.0f);
    for (int i = 0; i < PANEL_WIDTH * PANEL_HEIGHT; i++) {
        uint8_t byte = framebuf[i >> 3];
        uint8_t bit  = (byte >> (7 - (i & 7))) & 0x01;
        int x = i % PANEL_WIDTH;
        int y = i / PANEL_WIDTH;
        if (bit) {
            dma_display->drawPixelRGB888(x, y, cr, cg, cb);
        } else {
            dma_display->drawPixelRGB888(x, y, 0, 0, 0);
        }
    }
}

static void renderFingertips() {
    // 3x3 block per tip single pixels are invisible at 2mm pitch
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

void setup() {
    Serial.setRxBufferSize(RX_BUFFER_SIZE);
    Serial.begin(SERIAL_BAUD);

    analogReadResolution(12);
    analogSetPinAttenuation(POT_PIN, ADC_11db);

    pinMode(BUTTON_PIN, INPUT);   // external pull up required GPIO 34 has no internal pull up 

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

void loop() {
    // asymmetric debounce works even if external pull up is flaky 
    // Real press solid LOW Noise HIGH LOW jitter If we see any HIGH it s released 
    bool reading = digitalRead(BUTTON_PIN);
    if (reading == HIGH) {
        debouncedButtonState = HIGH;
        lastDebounceMs = millis(); // reset so noise LOWs don t accumulate
    }
    else if (reading == LOW && debouncedButtonState == HIGH) {
        // 100ms solid LOW no HIGH spikes real press
        if ((millis() - lastDebounceMs) > 100) {
            debouncedButtonState = LOW;
            currentMode = (currentMode == MODE_MASK) ? MODE_FINGERTIPS : MODE_MASK;
            fb_dirty = true;
            Serial.write(currentMode == MODE_MASK ? MODE_NOTIFY_0 : MODE_NOTIFY_1);
        }
    }

    // RX
    RxResult r = pollFrame();
    if (r == RX_OK) {
        Serial.write(ACK_BYTE);
        fb_dirty    = true;
        lastFrameMs = millis();
    } else if (r == RX_CRC_ERROR || r == RX_SYNC_ERROR) {
        Serial.write(NAK_BYTE);
    }

    // pot EWMA
    int raw = analogRead(POT_PIN);
    if (pot_ewma < 0.0f) pot_ewma = (float)raw;
    pot_ewma = 0.1f * (float)raw + 0.9f * pot_ewma;
    float t = pot_ewma / 4095.0f;
    if (t < 0.0f) t = 0.0f; else if (t > 1.0f) t = 1.0f;

    // tell the host so its preview matches rate limit to 50ms 
    int potByte = (int)(t * 255.0f + 0.5f);
    if (potByte < 0) potByte = 0; else if (potByte > 255) potByte = 255;
    if (lastReportedPot < 0 ||
        (abs(potByte - lastReportedPot) >= 1 && (millis() - lastPotReportMs) >= 50)) {
        uint8_t pkt[2] = { POT_NOTIFY, (uint8_t)potByte };
        Serial.write(pkt, 2);
        lastReportedPot = potByte;
        lastPotReportMs = millis();
    }

    // render
    bool t_changed = (last_t < 0.0f) || (fabsf(t - last_t) >= 0.01f);
    if (fb_dirty || (currentMode == MODE_MASK && t_changed)) {
        last_t = t;   // update before render so renderMask never sees stale t
        blankPanel();
        if (currentMode == MODE_MASK) {
            renderMask(t);
        } else {
            renderFingertips();
        }
        fb_dirty = false;
    }

    // watchdog blank if host stopped sending mode 0 only mode 1 stays on 
    if (currentMode == MODE_MASK && (millis() - lastFrameMs) > WATCHDOG_MS) {
        memset(framebuf, 0, PAYLOAD_BYTES);
        blankPanel();
        lastFrameMs = millis();
        last_t      = -1.0f;
    }
}
