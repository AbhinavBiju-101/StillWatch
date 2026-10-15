// Tunable settings. Secrets (WiFi, token) live in secrets.h, which is never committed.
#pragma once

#define FW_VERSION "0.4.0"

// ---- Pins (ESP32-S2 DevKitM-1). ADC1 pins keep working while WiFi is on.
#define PIN_PH       1
#define PIN_TDS      2
#define PIN_TURB     3
#define PIN_I2C_SDA  8
#define PIN_I2C_SCL  9
#define PIN_LED_R    4
#define PIN_LED_G    5
#define PIN_LED_B    6          // unused for now; reserved
#define PIN_BUZZER   7          // passive buzzer

#define LED_COMMON_ANODE  0     // 1 if the RGB LED's long leg goes to 3.3V (then LOW = on)
#define LCD_I2C_ADDR      0x27  // common alternatives: 0x3F. The serial log prints what it finds.

// ---- Analog front end
#define DIVIDER_RATIO     2.0f  // two equal resistors (10k/10k): multiply pin voltage by 2
#define N_SAMPLES         64    // oversampling per reading
#define HAVE_TDS          0     // set to 1 when the TDS probe is wired to PIN_TDS

// ---- Timing
#define READ_PERIOD_MS    10000UL   // sample sensors, refresh LCD/LEDs
#define SEND_PERIOD_MS    60000UL   // queue one reading for upload
#define HTTP_TIMEOUT_MS   60000     // long: a sleeping Render free service wakes slowly
#define QUEUE_CAPACITY    120       // unsent readings kept in RAM (2 h at 1/min)

// ---- Local safety rules (work with no WiFi). Keep in sync with app/detectors.py LIMITS.
#define PH_MIN            6.5f
#define PH_MAX            8.5f
#define TURB_MAX_NTU      5.0f

// ---- Networking
#define TLS_INSECURE      1     // 1 = skip certificate check (bench only). Use 0 + ROOT_CA_PEM for deployment.
