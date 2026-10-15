// Step 1: "Trust the sensors" - serial output only, no WiFi, no server.
// Board: ESP32-S2 DevKitM-1 (Arduino core). Open Serial Monitor at 115200 baud.
//
// Prints one CSV line per second. Copy the lines into a spreadsheet to compare
// tap water vs soil/milk, vinegar vs baking soda, etc.
//
// WIRING (each 0-5V sensor output goes through a voltage divider first):
//   sensor AO --[10k]--+--[10k]-- GND
//                      |
//                      +--> ESP32 GPIO   (pin sees half the sensor voltage)
//   pH        -> GPIO 1
//   TDS       -> GPIO 2   (sensor not arrived yet; leave disabled)
//   Turbidity -> GPIO 3
// Power the sensor modules from the 5V pin; share GND with the ESP32.

const int PIN_PH   = 1;
const int PIN_TDS  = 2;
const int PIN_TURB = 3;

const bool TDS_CONNECTED = false;      // set true once the TDS sensor is wired
const float DIVIDER_RATIO = 2.0;       // 10k/10k divider => multiply back by 2
const int   N_SAMPLES = 64;            // oversampling to cut ADC noise
const int   SAMPLE_DELAY_US = 500;
const unsigned long PERIOD_MS = 1000;

struct Reading {
  float pin_mv;   // average millivolts seen at the ESP32 pin
  float sensor_mv; // pin_mv corrected for the divider (= module output)
  float spread_mv; // max-min across the samples (noise indicator)
};

Reading readChannel(int pin) {
  long sum = 0;
  int lo = 100000, hi = 0;
  for (int i = 0; i < N_SAMPLES; i++) {
    int mv = analogReadMilliVolts(pin);   // factory-calibrated by the core
    sum += mv;
    if (mv < lo) lo = mv;
    if (mv > hi) hi = mv;
    delayMicroseconds(SAMPLE_DELAY_US);
  }
  Reading r;
  r.pin_mv = (float)sum / N_SAMPLES;
  r.sensor_mv = r.pin_mv * DIVIDER_RATIO;
  r.spread_mv = hi - lo;
  return r;
}

void setup() {
  Serial.begin(115200);
  delay(1500);
  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);   // ~0-3.1V usable range
  Serial.println("ms,ph_pin_mv,ph_mv,ph_spread,turb_pin_mv,turb_mv,turb_spread,tds_pin_mv,tds_mv");
}

void loop() {
  unsigned long t0 = millis();
  Reading ph   = readChannel(PIN_PH);
  Reading turb = readChannel(PIN_TURB);
  Reading tds  = {0, 0, 0};
  if (TDS_CONNECTED) tds = readChannel(PIN_TDS);

  Serial.printf("%lu,%.1f,%.1f,%.1f,%.1f,%.1f,%.1f,%.1f,%.1f\n",
                t0, ph.pin_mv, ph.sensor_mv, ph.spread_mv,
                turb.pin_mv, turb.sensor_mv, turb.spread_mv,
                tds.pin_mv, tds.sensor_mv);

  long wait = PERIOD_MS - (millis() - t0);
  if (wait > 0) delay(wait);
}
