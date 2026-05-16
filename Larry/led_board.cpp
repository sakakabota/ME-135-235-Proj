#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "ESP32-HUB75-MatrixPanel-I2S-DMA.h"

// Custom Pin Definitions 
// Data Pins
#define R1_PIN 25
#define G1_PIN 26
#define B1_PIN 27
#define R2_PIN 14
#define G2_PIN 12
#define B2_PIN 13

// Control Pins
#define A_PIN 19
#define B_PIN 22
#define C_PIN 5
#define D_PIN 33
#define E_PIN 21 // Updated to 21

#define CLK_PIN 20
#define LAT_PIN 4
#define OE_PIN 15

// Matrix Setup 
#define PANEL_RES_X 64 // Width
#define PANEL_RES_Y 64 // Height
#define PANEL_CHAIN 1  // Number of panels

MatrixPanel_I2S_DMA *dma_display = nullptr;

// Main entry point for ESP IDF
extern "C" void app_main(void)
{

    // 1 Map your custom pins to the library s structure
    HUB75_I2S_CFG::i2s_pins _custom_pins = {
        R1_PIN, G1_PIN, B1_PIN, R2_PIN, G2_PIN, B2_PIN,
        A_PIN, B_PIN, C_PIN, D_PIN, E_PIN,
        LAT_PIN, OE_PIN, CLK_PIN};

    // 2 Configure the matrix dimensions and pass in the custom pins
    HUB75_I2S_CFG mxconfig(
        PANEL_RES_X,
        PANEL_RES_Y,
        PANEL_CHAIN,
        _custom_pins);

    // 3 Initialize the display
    dma_display = new MatrixPanel_I2S_DMA(mxconfig);
    dma_display->begin();

    // Set brightness 0 255 Kept low for testing 
    dma_display->setBrightness8(60);
    dma_display->clearScreen();

    // 4 Draw to the screen 

    // Red border
    dma_display->drawRect(0, 0, dma_display->width(), dma_display->height(), dma_display->color565(255, 0, 0));

    // Blue X 
    dma_display->drawLine(0, 0, dma_display->width(), dma_display->height(), dma_display->color565(0, 0, 255));
    dma_display->drawLine(dma_display->width(), 0, 0, dma_display->height(), dma_display->color565(0, 0, 255));

    // Green Text
    dma_display->setTextSize(1);
    dma_display->setTextColor(dma_display->color565(0, 255, 0));
    dma_display->setCursor(18, 28);
    dma_display->print("ESP32");

    // 5 FreeRTOS loop to keep the program running
    while (1)
    {
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}