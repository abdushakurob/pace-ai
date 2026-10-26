#include <Arduino_RouterBridge.h>
#include <Arduino_LED_Matrix.h>
#include <vector>

Arduino_LED_Matrix matrix;

const uint8_t FRAME_SIZE = 8 * 13;
uint8_t frame[FRAME_SIZE] = {0};
String paceState = "ready";

void renderPaceSignal() {
    memset(frame, 0, sizeof(frame));
    const uint8_t phase = (millis() / 120) % 13;
    if (paceState == "thinking") {
        for (uint8_t row = 0; row < 8; row++) frame[row * 13 + phase] = 6;
    } else if (paceState == "error") {
        const bool on = ((millis() / 350) % 2) == 0;
        if (on) for (uint8_t i = 0; i < 8; i++) frame[i * 13 + 6] = 7;
    } else {
        frame[3 * 13 + 6] = 7;
        frame[4 * 13 + 6] = 5;
    }
}

void setup() {
    matrix.begin();
    matrix.setGrayscaleBits(3);
    matrix.clear();
    Bridge.begin();
    Bridge.provide("draw", draw);
    Bridge.provide("pace_signal", paceSignal);
}

void loop() {
    renderPaceSignal();
    matrix.draw(frame);
    delay(30);
}

void paceSignal(String next) {
    paceState = next;
}

void draw(std::vector<uint8_t> next) {
    size_t len = min(next.size(), (size_t)FRAME_SIZE);
    memcpy(frame, next.data(), len);
}
