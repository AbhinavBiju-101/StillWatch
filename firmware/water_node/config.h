// Tunable settings. Secrets (WiFi, token) live in secrets.h, which is never committed.
#pragma once

#define FW_VERSION "0.5.0"

// ---- Pins (ESP32-S2 DevKitM-1). ADC1 pins keep working while WiFi is on.
#define PIN_PH       1
#define PIN_TDS      2
#define PIN_TURB     3
#define PIN_ORP      10         // analog ORP module output (ADC1)
#define PIN_ONEWIRE  11         // DS18B20 data pin, 4.7 k pull-up to 3.3 V
#define PIN_I2C_SDA  8
#define PIN_I2C_SCL  9
#define PIN_LED_R    4
#define PIN_LED_G    5
#define PIN_LED_B    6          // unused for now; reserved
#define PIN_BUZZER   7          // passive buzzer
#define PIN_PORTAL_BTN 0        // the board's BOOT button: hold 3 s at power-up to open the WiFi setup portal

#define LED_COMMON_ANODE  0     // 1 if the RGB LED's long leg goes to 3.3V (then LOW = on)
#define LCD_I2C_ADDR      0x27  // common alternatives: 0x3F. The serial log prints what it finds.

// ---- Which sensors are wired. A sensor set to 0 is simply left out of every message.
#define HAVE_TDS          0     // TDS probe on PIN_TDS
#define HAVE_TEMP         0     // DS18B20 on PIN_ONEWIRE (needs the OneWire + DallasTemperature libraries)
#define HAVE_ORP          0     // analog ORP module on PIN_ORP

// ---- Analog front end
#define DIVIDER_RATIO     2.0f  // two equal resistors (10k/10k): multiply pin voltage by 2
#define N_SAMPLES         64    // oversampling per reading
#define TRIM_SAMPLES      6     // lowest and highest samples thrown away before averaging
#define FAULT_LO_MV       20.0f   // a sensor output stuck below this (or above FAULT_HI_MV) is reported as a fault
#define FAULT_HI_MV       4950.0f
#define FAULT_SPREAD_MV   250.0f  // after trimming, samples this far apart mean a floating or noisy input
#define ORP_DIVIDER_RATIO 1.0f  // the ORP module outputs 0-3 V, so no divider is needed
#define ORP_ZERO_MV       2500.0f // module output at 0 mV ORP (DFRobot-style analog ORP meter)
#define ORP_GAIN          1.037f

// ---- Timing (the server can change the two periods at check-in)
#define READ_PERIOD_MS    10000UL   // sample sensors, refresh LCD/LEDs
#define SEND_PERIOD_MS    60000UL   // store one reading for upload
#define CHECKIN_PERIOD_MS 300000UL  // heartbeat + settings + firmware offer
#define HTTP_TIMEOUT_MS   60000     // long: a sleeping Render free service wakes slowly
#define BATCH_MAX         20        // readings per upload request

// ---- Offline buffer in flash (LittleFS). 64 bytes per reading, so 4000 is about 250 KB: nearly 3 days at 1/min.
#define QUEUE_CAPACITY    4000

// ---- Local safety rules (work with no WiFi). These are only the first-boot defaults:
// the server sends its current limits at every check-in and the node remembers them.
#define PH_MIN            6.5f
#define PH_MAX            8.5f
#define TURB_MAX_NTU      5.0f

// ---- Networking
#define TLS_INSECURE      1     // 1 = skip certificate check (bench only). Use 0 + ROOT_CA_PEM for deployment.
#define PORTAL_AFTER_FAILS 24   // WiFi attempts (about 15 s each) before the setup portal opens by itself
#define PORTAL_SECONDS    180   // how long the setup portal stays open

// ---- Over-the-air updates. Off by default: a node that can replace its own firmware is something to turn on
// deliberately. Needs TLS_INSECURE 0, so the download cannot be swapped in transit; the image's SHA-256 (from the
// check-in reply) is checked before the new firmware is activated. OTA_ALLOW_INSECURE 1 is for bench testing only.
#define OTA_ENABLED       0
#define OTA_ALLOW_INSECURE 0
