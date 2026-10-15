// Water-quality node: reads pH + turbidity (+ TDS when wired), shows status on the LCD/LED/buzzer,
// and uploads readings to the Flask backend. Local alerts never wait on the network.
//
// Needs: arduino-esp32 core 3.x, library "LiquidCrystal I2C" (Frank de Brabander).
// Board in Arduino IDE: "ESP32S2 Dev Module". If the Serial Monitor is blank, set
// Tools > USB CDC On Boot > Enabled (or use the board's UART USB port).
//
// Serial commands (115200 baud): cal4, cal7, clearcal, status, ledtest
//   Put the pH probe in the pH 4 buffer, wait ~1 min, send "cal4". Rinse, pH 7 buffer, send "cal7".
//   Until both are done pH is reported as "--" and is left out of uploads and local rules.

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <Preferences.h>
#include <math.h>
#include <time.h>
#include "config.h"
#include "secrets.h"

// ------------------------------------------------------------------ data

struct Sample {
  time_t ts;        // 0 = clock not synced; the server then stamps its own time
  float ph;         // NAN until calibrated
  float ph_mv;
  float ntu;
  float turb_mv;
  float tds;        // NAN when no TDS probe
};

enum NetState { NET_OFFLINE, NET_OK, NET_AUTH };

LiquidCrystal_I2C lcd(LCD_I2C_ADDR, 16, 2);
Preferences prefs;

volatile NetState netState = NET_OFFLINE;
Sample latest;
bool haveReading = false;
bool alertRed = false;
unsigned long lastRead = 0, lastSend = 0, lastBeep = 0;
bool everSent = false;

bool phCalibrated = false;
float phSlope = 0, phOffset = 0;

// Ring buffer of unsent samples, shared with the network task.
Sample queueBuf[QUEUE_CAPACITY];
int qHead = 0, qCount = 0;
SemaphoreHandle_t qMutex;

void setLed(bool r, bool g);               // defined below; used by the serial commands

// --------------------------------------------------------------- queue

void qPush(const Sample &s) {
  xSemaphoreTake(qMutex, portMAX_DELAY);
  if (qCount == QUEUE_CAPACITY) {          // full: drop the oldest
    qHead = (qHead + 1) % QUEUE_CAPACITY;
    qCount--;
  }
  queueBuf[(qHead + qCount) % QUEUE_CAPACITY] = s;
  qCount++;
  xSemaphoreGive(qMutex);
}

bool qPeek(Sample &out) {
  bool ok = false;
  xSemaphoreTake(qMutex, portMAX_DELAY);
  if (qCount > 0) { out = queueBuf[qHead]; ok = true; }
  xSemaphoreGive(qMutex);
  return ok;
}

void qPop() {
  xSemaphoreTake(qMutex, portMAX_DELAY);
  if (qCount > 0) { qHead = (qHead + 1) % QUEUE_CAPACITY; qCount--; }
  xSemaphoreGive(qMutex);
}

int qSize() {
  xSemaphoreTake(qMutex, portMAX_DELAY);
  int n = qCount;
  xSemaphoreGive(qMutex);
  return n;
}

// -------------------------------------------------------------- sensors

// Average of N_SAMPLES ADC reads, in millivolts at the SENSOR output (divider undone).
float readSensorMv(int pin) {
  long sum = 0;
  for (int i = 0; i < N_SAMPLES; i++) {
    sum += analogReadMilliVolts(pin);
    delayMicroseconds(500);
  }
  return ((float)sum / N_SAMPLES) * DIVIDER_RATIO;
}

float phFromMv(float mv) {
  return phCalibrated ? phSlope * mv + phOffset : NAN;
}

// Placeholder: the common datasheet curve for hobby turbidity modules (clear water ~4.2 V).
// Replace with a calibration against known turbidity standards; raw mV is always uploaded.
float ntuFromMv(float mv) {
  float v = mv / 1000.0f;
  if (v >= 4.2f) return 0.0f;
  if (v < 2.5f) return 3000.0f;
  float ntu = -1120.4f * v * v + 5742.3f * v - 4352.9f;
  return ntu < 0 ? 0 : ntu;
}

#if HAVE_TDS
// Common datasheet polynomial for hobby TDS modules, no temperature compensation yet.
float tdsFromMv(float mv) {
  float v = mv / 1000.0f;
  return (133.42f * v * v * v - 255.86f * v * v + 857.39f * v) * 0.5f;
}
#endif

// ----------------------------------------------------------- calibration

void computePhCal(float mv4, float mv7) {
  if (isnan(mv4) || isnan(mv7) || fabsf(mv7 - mv4) < 20.0f) { phCalibrated = false; return; }
  phSlope = 3.0f / (mv7 - mv4);
  phOffset = 7.0f - phSlope * mv7;
  phCalibrated = true;
}

void loadCalibration() {
  prefs.begin("cal", true);
  float mv4 = prefs.getFloat("mv4", NAN);
  float mv7 = prefs.getFloat("mv7", NAN);
  prefs.end();
  computePhCal(mv4, mv7);
}

void saveCalPoint(const char *key, float mv) {
  prefs.begin("cal", false);
  prefs.putFloat(key, mv);
  float mv4 = prefs.getFloat("mv4", NAN);
  float mv7 = prefs.getFloat("mv7", NAN);
  prefs.end();
  computePhCal(mv4, mv7);
}

void handleCommand(const char *cmd) {
  if (strcmp(cmd, "cal4") == 0 || strcmp(cmd, "cal7") == 0) {
    float mv = readSensorMv(PIN_PH);
    saveCalPoint(strcmp(cmd, "cal4") == 0 ? "mv4" : "mv7", mv);
    Serial.printf("Saved %s = %.1f mV. pH calibrated: %s\n", cmd, mv, phCalibrated ? "yes" : "not yet");
  } else if (strcmp(cmd, "clearcal") == 0) {
    prefs.begin("cal", false); prefs.clear(); prefs.end();
    phCalibrated = false;
    Serial.println("Calibration cleared.");
  } else if (strcmp(cmd, "ledtest") == 0) {
    // Expect: red for 1 s, green for 1 s, off. If nothing lights, the LED may be wired/typed the other way
    // round: flip LED_COMMON_ANODE in config.h (see firmware/README.md).
    Serial.println("LED test: red, then green");
    setLed(true, false);  delay(1000);
    setLed(false, true);  delay(1000);
    setLed(alertRed, !alertRed);
  } else if (strcmp(cmd, "status") == 0) {
    Serial.printf("wifi=%d net=%d queued=%d calibrated=%d time_synced=%d\n",
                  WiFi.status() == WL_CONNECTED, (int)netState, qSize(), phCalibrated,
                  time(nullptr) > 1700000000);
  } else if (cmd[0]) {
    Serial.println("Commands: cal4, cal7, clearcal, status, ledtest");
  }
}

void pollSerial() {
  static char buf[24];
  static int n = 0;
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') { buf[n] = 0; handleCommand(buf); n = 0; }
    else if (n < (int)sizeof(buf) - 1) buf[n++] = c;
  }
}

// ----------------------------------------------------------------- outputs

void setLed(bool r, bool g) {
  bool on = !LED_COMMON_ANODE;
  digitalWrite(PIN_LED_R, r ? on : !on);
  digitalWrite(PIN_LED_G, g ? on : !on);
  digitalWrite(PIN_LED_B, !on);              // blue off
}

void showLcd() {
  char l0[17], l1[17];
  if (isnan(latest.ph)) snprintf(l0, sizeof l0, "pH --  N %.1f", latest.ntu);
  else                  snprintf(l0, sizeof l0, "pH %.2f N %.1f", latest.ph, latest.ntu);
  if (alertRed) snprintf(l1, sizeof l1, "TEST RECOMMENDED");
  else          snprintf(l1, sizeof l1, "No alert %s q%d", netState == NET_OK ? "ok" : "--", qSize());
  lcd.setCursor(0, 0); lcd.printf("%-16s", l0);
  lcd.setCursor(0, 1); lcd.printf("%-16s", l1);
}

// Local rule: the same hard limits as the backend. Works with no network.
bool outsideLimits(const Sample &s) {
  if (!isnan(s.ph) && (s.ph < PH_MIN || s.ph > PH_MAX)) return true;
  return s.ntu > TURB_MAX_NTU;
}

void takeReading() {
  Sample s;
  s.ph_mv = readSensorMv(PIN_PH);
  s.turb_mv = readSensorMv(PIN_TURB);
  s.ph = phFromMv(s.ph_mv);
  s.ntu = ntuFromMv(s.turb_mv);
#if HAVE_TDS
  s.tds = tdsFromMv(readSensorMv(PIN_TDS));
#else
  s.tds = NAN;
#endif
  time_t t = time(nullptr);
  s.ts = t > 1700000000 ? t : 0;

  latest = s;
  haveReading = true;
  alertRed = outsideLimits(s);
  setLed(alertRed, !alertRed);
  showLcd();
  Serial.printf("ph=%.2f (%.0f mV)  turb=%.2f NTU (%.0f mV)  alert=%d  queued=%d\n",
                s.ph, s.ph_mv, s.ntu, s.turb_mv, alertRed, qSize());
}

// ------------------------------------------------------------- networking

int postSample(const Sample &s) {
  char body[360];
  int n = snprintf(body, sizeof body, "{\"schema\":1,\"device_id\":\"%s\"", DEVICE_ID);
  if (s.ts) {
    struct tm tmv;
    gmtime_r(&s.ts, &tmv);
    n += strftime(body + n, sizeof body - n, ",\"ts\":\"%Y-%m-%dT%H:%M:%SZ\"", &tmv);
  }
  if (!isnan(s.ph)) n += snprintf(body + n, sizeof body - n, ",\"ph\":%.2f", s.ph);
  n += snprintf(body + n, sizeof body - n, ",\"ph_raw_mv\":%.0f,\"turbidity_ntu\":%.2f,\"turbidity_raw_mv\":%.0f",
                s.ph_mv, s.ntu, s.turb_mv);
  if (!isnan(s.tds)) n += snprintf(body + n, sizeof body - n, ",\"tds_ppm\":%.0f", s.tds);
  snprintf(body + n, sizeof body - n, ",\"fw\":\"%s\"}", FW_VERSION);

  char url[160];
  snprintf(url, sizeof url, "%s/api/readings", SERVER_URL);

  HTTPClient http;
  WiFiClientSecure secure;
  WiFiClient plain;
  if (strncmp(SERVER_URL, "https", 5) == 0) {
#if TLS_INSECURE
    secure.setInsecure();
#else
    secure.setCACert(ROOT_CA_PEM);
#endif
    http.begin(secure, url);
  } else {
    http.begin(plain, url);
  }
  http.setTimeout(HTTP_TIMEOUT_MS);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Device-Token", DEVICE_TOKEN);
  int code = http.POST((uint8_t *)body, strlen(body));
  http.end();
  return code;                               // negative = could not reach the server
}

bool connectWifi() {
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  for (int i = 0; i < 30 && WiFi.status() != WL_CONNECTED; i++) vTaskDelay(pdMS_TO_TICKS(500));
  if (WiFi.status() != WL_CONNECTED) return false;
  configTime(0, 0, "pool.ntp.org", "time.google.com");   // UTC; used for reading timestamps
  return true;
}

// Runs beside loop(): uploads queued samples oldest-first and retries with the SAME timestamp,
// so the server's duplicate check makes retries harmless.
void netTask(void *) {
  for (;;) {
    if (WiFi.status() != WL_CONNECTED) {
      netState = NET_OFFLINE;
      if (!connectWifi()) { vTaskDelay(pdMS_TO_TICKS(5000)); continue; }
    }
    Sample s;
    if (!qPeek(s)) { vTaskDelay(pdMS_TO_TICKS(1000)); continue; }

    int code = postSample(s);
    if (code == 200 || code == 201) {
      qPop(); netState = NET_OK;
    } else if (code == 400 || code == 409) {
      Serial.printf("Server rejected a reading (%d); dropping it.\n", code);
      qPop();
    } else if (code == 401) {
      netState = NET_AUTH;
      Serial.println("Server says 401: check DEVICE_TOKEN.");
      vTaskDelay(pdMS_TO_TICKS(30000));
    } else {
      netState = NET_OFFLINE;
      vTaskDelay(pdMS_TO_TICKS(5000));
    }
    vTaskDelay(pdMS_TO_TICKS(100));
  }
}

// ------------------------------------------------------------------ setup/loop

void setup() {
  Serial.begin(115200);
  delay(1000);
  pinMode(PIN_LED_R, OUTPUT); pinMode(PIN_LED_G, OUTPUT); pinMode(PIN_LED_B, OUTPUT);
  pinMode(PIN_BUZZER, OUTPUT);
  setLed(false, false);

  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);

  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL);
  lcd.init();
  lcd.backlight();
  lcd.setCursor(0, 0); lcd.print("Water node ");
  lcd.setCursor(0, 1); lcd.print("v" FW_VERSION);

  loadCalibration();
  Serial.printf("Water node %s, device %s, pH calibrated: %s\n", FW_VERSION, DEVICE_ID,
                phCalibrated ? "yes" : "no (use cal4 / cal7)");

  qMutex = xSemaphoreCreateMutex();
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  xTaskCreate(netTask, "net", 8192, nullptr, 1, nullptr);
}

void loop() {
  pollSerial();
  unsigned long now = millis();

  if (now - lastRead >= READ_PERIOD_MS || !haveReading) {
    lastRead = now;
    takeReading();
  }
  if (haveReading && (!everSent || now - lastSend >= SEND_PERIOD_MS)) {
    lastSend = now;
    everSent = true;
    qPush(latest);
  }
  if (alertRed && now - lastBeep >= 5000) {
    lastBeep = now;
    tone(PIN_BUZZER, 2000, 300);
  }
  delay(20);
}
