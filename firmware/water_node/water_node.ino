// StillWatch water-quality node: reads pH, turbidity (+ TDS, temperature, ORP when wired), shows status on the
// LCD/LED/buzzer, keeps every reading in a flash queue, and uploads them to the Flask backend in batches.
// Local alerts never wait on the network, and no reading is lost to an outage or a reboot.
//
// Needs: arduino-esp32 core 3.x, library "LiquidCrystal I2C" (Frank de Brabander); for HAVE_TEMP also the
// "OneWire" and "DallasTemperature" libraries. Board in Arduino IDE: "ESP32S2 Dev Module". If the Serial
// Monitor is blank, set Tools > USB CDC On Boot > Enabled (or use the board's UART USB port).
//
// Files: logic.h (conversions, JSON, settings; unit-tested on a PC), queue.h (flash queue; unit-tested),
// fsstore.h (LittleFS adapter), net.h (upload, check-in, OTA), portal.h (WiFi setup portal).
//
// Serial commands (115200 baud): cal4, cal7, clearcal, calorp <mV>, status, info, ledtest, portal, clearq, reboot
//   Put the pH probe in the pH 4 buffer, wait ~1 min, send "cal4". Rinse, pH 7 buffer, send "cal7".
//   Until both are done pH is reported as "--" and is left out of uploads and local rules.

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <Preferences.h>
#include <Update.h>
#include <WebServer.h>
#include <DNSServer.h>
#include <esp_system.h>
#include <mbedtls/sha256.h>
#include <math.h>
#include <time.h>
#include <sys/time.h>
#include "config.h"
#include "secrets.h"
#include "logic.h"
#include "queue.h"
#include "fsstore.h"
#if HAVE_TEMP
#include <OneWire.h>
#include <DallasTemperature.h>
#endif

// ------------------------------------------------------------------ state

enum NetState { NET_OFFLINE, NET_OK, NET_AUTH };

LiquidCrystal_I2C lcd(LCD_I2C_ADDR, 16, 2);
Preferences prefs;
#if HAVE_TEMP
OneWire oneWire(PIN_ONEWIRE);
DallasTemperature tempSensor(&oneWire);
#endif

FlashStore flash;
PersistentQueue<FlashStore, QUEUE_CAPACITY> queue(flash);
SemaphoreHandle_t qMutex;                   // the queue is touched by loop() and by the network task

volatile NetState netState = NET_OFFLINE;
Settings settings = {READ_PERIOD_MS, SEND_PERIOD_MS, PH_MIN, PH_MAX, TURB_MAX_NTU};
PhCal phCal;
float orpOffsetMv = 0;

Rec latest;
bool haveReading = false;
bool alertRed = false;
volatile bool storageFault = false;
volatile uint8_t currentFaults = 0;         // what the last reading said; sent with every upload and check-in
unsigned long lastRead = 0, lastStore = 0, lastBeep = 0, lastOkMs = 0;
bool everStored = false;
uint32_t bootId = 0;
const char *bootReason = "other";

// Where this node talks to. Saved setup-portal values win over the compile-time defaults in secrets.h.
struct NetCfg { char ssid[33], pass[65], url[96], token[72], dev[65]; } netCfg;

volatile bool portalRequested = false, portalActive = false;
char portalL0[17] = "", portalL1[17] = "";

void setLed(bool r, bool g);                // defined below; used by the serial commands

// ------------------------------------------------------------ small helpers

void qLock()   { xSemaphoreTake(qMutex, portMAX_DELAY); }
void qUnlock() { xSemaphoreGive(qMutex); }

void loadNetCfg() {
  prefs.begin("net", true);
  strlcpy(netCfg.ssid,  prefs.getString("ssid",  WIFI_SSID).c_str(),   sizeof netCfg.ssid);
  strlcpy(netCfg.pass,  prefs.getString("pass",  WIFI_PASS).c_str(),   sizeof netCfg.pass);
  strlcpy(netCfg.url,   prefs.getString("url",   SERVER_URL).c_str(),  sizeof netCfg.url);
  strlcpy(netCfg.token, prefs.getString("token", DEVICE_TOKEN).c_str(), sizeof netCfg.token);
  strlcpy(netCfg.dev,   prefs.getString("dev",   DEVICE_ID).c_str(),   sizeof netCfg.dev);
  prefs.end();
}

void loadSettings() {
  prefs.begin("set", true);
  settings.read_ms  = prefs.getUInt("rd", settings.read_ms);
  settings.send_ms  = prefs.getUInt("sd", settings.send_ms);
  settings.ph_min   = prefs.getFloat("pmin", settings.ph_min);
  settings.ph_max   = prefs.getFloat("pmax", settings.ph_max);
  settings.turb_max = prefs.getFloat("tmax", settings.turb_max);
  prefs.end();
}

void saveSettings() {
  prefs.begin("set", false);
  prefs.putUInt("rd", settings.read_ms);
  prefs.putUInt("sd", settings.send_ms);
  prefs.putFloat("pmin", settings.ph_min);
  prefs.putFloat("pmax", settings.ph_max);
  prefs.putFloat("tmax", settings.turb_max);
  prefs.end();
}

const char *resetReasonText() {
  switch (esp_reset_reason()) {
    case ESP_RST_POWERON:   return "power_on";
    case ESP_RST_SW:        return "software";
    case ESP_RST_PANIC:     return "panic";
    case ESP_RST_INT_WDT:
    case ESP_RST_TASK_WDT:
    case ESP_RST_WDT:       return "watchdog";
    case ESP_RST_BROWNOUT:  return "brownout";
    default:                return "other";
  }
}

// -------------------------------------------------------------- sensors

struct Adc { float mv; float spread; };

// Trimmed mean of N_SAMPLES conversions, in millivolts at the SENSOR output (divider undone).
Adc readAdc(int pin, float divider) {
  uint16_t a[N_SAMPLES];
  for (int i = 0; i < N_SAMPLES; i++) {
    a[i] = (uint16_t)analogReadMilliVolts(pin);
    delayMicroseconds(300);
  }
  float spread;
  float mv = trimmedMean(a, N_SAMPLES, TRIM_SAMPLES, &spread);
  return {mv * divider, spread * divider};
}

bool adcFault(const Adc &a) { return sensorFault(a.mv, a.spread, FAULT_LO_MV, FAULT_HI_MV, FAULT_SPREAD_MV); }

// ----------------------------------------------------------- calibration

void loadCalibration() {
  prefs.begin("cal", true);
  float mv4 = prefs.getFloat("mv4", NAN), mv7 = prefs.getFloat("mv7", NAN);
  float t4 = prefs.getFloat("t4", NAN), t7 = prefs.getFloat("t7", NAN);
  orpOffsetMv = prefs.getFloat("orp", 0);
  prefs.end();
  float temp = NAN;                         // temperature of the buffers: the mean of what was recorded
  if (tempValid(t4) && tempValid(t7)) temp = (t4 + t7) / 2; else if (tempValid(t4)) temp = t4; else if (tempValid(t7)) temp = t7;
  phCal = phCalFromPoints(mv4, mv7, temp);
}

void saveCalPoint(const char *mvKey, const char *tKey, float mv, float tempC) {
  prefs.begin("cal", false);
  prefs.putFloat(mvKey, mv);
  prefs.putFloat(tKey, tempC);              // NAN when there is no temperature probe: no compensation then
  prefs.end();
  loadCalibration();
}

float currentTemp() { return latest.temp; }

void handleCommand(char *cmd) {
  char *arg = strchr(cmd, ' ');
  if (arg) { *arg++ = 0; while (*arg == ' ') arg++; }
  if (strcmp(cmd, "cal4") == 0 || strcmp(cmd, "cal7") == 0) {
    Adc a = readAdc(PIN_PH, DIVIDER_RATIO);
    bool is4 = strcmp(cmd, "cal4") == 0;
    saveCalPoint(is4 ? "mv4" : "mv7", is4 ? "t4" : "t7", a.mv, currentTemp());
    Serial.printf("Saved %s = %.1f mV (spread %.0f). pH calibrated: %s%s\n", cmd, a.mv, a.spread,
                  phCal.valid ? "yes" : "not yet", isnan(phCal.calTempC) ? "" : ", temperature-compensated");
  } else if (strcmp(cmd, "calorp") == 0 && arg && *arg) {
    // Probe in an ORP standard of known value (in mV): stores the offset that makes the reading match.
    float known = atof(arg);
    Adc a = readAdc(PIN_ORP, ORP_DIVIDER_RATIO);
    float raw = orpFromMv(a.mv, ORP_ZERO_MV, ORP_GAIN, 0);
    orpOffsetMv = raw - known;
    prefs.begin("cal", false); prefs.putFloat("orp", orpOffsetMv); prefs.end();
    Serial.printf("ORP offset set to %.1f mV (raw %.1f, standard %.1f)\n", orpOffsetMv, raw, known);
  } else if (strcmp(cmd, "clearcal") == 0) {
    prefs.begin("cal", false); prefs.clear(); prefs.end();
    phCal = PhCal(); orpOffsetMv = 0;
    Serial.println("Calibration cleared.");
  } else if (strcmp(cmd, "ledtest") == 0) {
    // Expect: red for 1 s, green for 1 s, off. If nothing lights, the LED may be wired/typed the other way
    // round: flip LED_COMMON_ANODE in config.h (see firmware/README.md).
    Serial.println("LED test: red, then green");
    setLed(true, false);  delay(1000);
    setLed(false, true);  delay(1000);
    setLed(alertRed, !alertRed);
  } else if (strcmp(cmd, "status") == 0) {
    qLock(); uint32_t qn = queue.count(); uint32_t lost = queue.dropped; qUnlock();
    Serial.printf("wifi=%d net=%d queued=%u lost=%u calibrated=%d time_synced=%d faults=0x%02x\n",
                  WiFi.status() == WL_CONNECTED, (int)netState, qn, lost, phCal.valid,
                  time(nullptr) > 1700000000, currentFaults);
  } else if (strcmp(cmd, "info") == 0) {
    Serial.printf("fw %s, device %s, server %s, ssid %s, boot %s\nread %lu ms, send %lu ms, pH limits %.1f-%.1f, turbidity limit %.1f NTU\n",
                  FW_VERSION, netCfg.dev, netCfg.url, netCfg.ssid, bootReason, (unsigned long)settings.read_ms,
                  (unsigned long)settings.send_ms, settings.ph_min, settings.ph_max, settings.turb_max);
  } else if (strcmp(cmd, "portal") == 0) {
    Serial.println("Opening the WiFi setup portal.");
    portalRequested = true;
  } else if (strcmp(cmd, "clearq") == 0) {
    qLock(); queue.clearAll(); qUnlock();
    Serial.println("Unsent readings cleared.");
  } else if (strcmp(cmd, "reboot") == 0) {
    ESP.restart();
  } else if (cmd[0]) {
    Serial.println("Commands: cal4, cal7, clearcal, calorp <mV>, status, info, ledtest, portal, clearq, reboot");
  }
}

void pollSerial() {
  static char buf[40];
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

void faultText(uint8_t f, char *out, size_t cap) {
  out[0] = 0;
  for (int i = 0; i < 7; i++)
    if (f & (1u << i)) { if (out[0]) strlcat(out, ",", cap); strlcat(out, FAULT_NAMES[i], cap); }
}

void showLcd() {
  char l0[40], l1[40];
  if (portalActive) {                        // the setup portal owns the display while it is open
    lcd.setCursor(0, 0); lcd.printf("%-16s", portalL0);
    lcd.setCursor(0, 1); lcd.printf("%-16s", portalL1);
    return;
  }
  bool extra = (HAVE_TEMP || HAVE_TDS || HAVE_ORP) && ((millis() / 5000) % 2);
  if (extra) {                               // second page: the optional sensors
    String s;
    if (!isnan(latest.temp)) s += "T" + String(latest.temp, 1) + " ";
    if (!isnan(latest.tds))  s += "D" + String(latest.tds, 0) + " ";
    if (!isnan(latest.orp))  s += "O" + String(latest.orp, 0);
    snprintf(l0, sizeof l0, "%s", s.c_str());
  } else if (isnan(latest.ph)) snprintf(l0, sizeof l0, "pH --  N %.1f", latest.ntu);
  else                         snprintf(l0, sizeof l0, "pH %.2f N %.1f", latest.ph, latest.ntu);

  if (alertRed) snprintf(l1, sizeof l1, "TEST RECOMMENDED");
  else if (currentFaults & 0x1F) {
    char f[32]; faultText(currentFaults & 0x1F, f, sizeof f);
    snprintf(l1, sizeof l1, "Check:%s", f);
  } else {
    qLock(); uint32_t qn = queue.count(); qUnlock();
    snprintf(l1, sizeof l1, "No alert %s q%u", netState == NET_OK ? "ok" : "--", (unsigned)qn);
  }
  lcd.setCursor(0, 0); lcd.printf("%-16.16s", l0);
  lcd.setCursor(0, 1); lcd.printf("%-16.16s", l1);
}

void takeReading() {
  Rec r;
  recInit(r);

  float tempC = NAN;
#if HAVE_TEMP
  tempSensor.requestTemperatures();          // blocks about 750 ms at 12 bits
  float t = tempSensor.getTempCByIndex(0);
  if (tempValid(t)) { r.temp = t; tempC = t; } else r.faults |= F_TEMP;
#endif

  Adc a = readAdc(PIN_PH, DIVIDER_RATIO);
  if (adcFault(a)) r.faults |= F_PH;
  else { r.ph_mv = a.mv; r.ph = phFromMv(a.mv, phCal, tempC); }     // NAN until calibrated

  a = readAdc(PIN_TURB, DIVIDER_RATIO);
  if (adcFault(a)) r.faults |= F_TURB;
  else { r.turb_mv = a.mv; r.ntu = ntuFromMv(a.mv); }

#if HAVE_TDS
  a = readAdc(PIN_TDS, DIVIDER_RATIO);
  if (adcFault(a)) r.faults |= F_TDS;
  else { r.tds_mv = a.mv; r.tds = tdsFromMv(a.mv, tempC); }
#endif
#if HAVE_ORP
  a = readAdc(PIN_ORP, ORP_DIVIDER_RATIO);
  if (adcFault(a)) r.faults |= F_ORP;
  else { r.orp_mv = a.mv; r.orp = orpFromMv(a.mv, ORP_ZERO_MV, ORP_GAIN, orpOffsetMv); }
#endif

  time_t now = time(nullptr);
  r.ts = now > 1700000000 ? (uint32_t)now : 0;
  r.boot_id = bootId;
  r.boot_s = millis() / 1000;
  if (!r.ts && millis() > 600000UL) r.faults |= F_CLOCK;             // no NTP for 10 minutes
  if (storageFault) r.faults |= F_STORAGE;

  latest = r;
  currentFaults = r.faults;
  haveReading = true;
  alertRed = outsideLimits(r, settings);
  setLed(alertRed, !alertRed);
  showLcd();
  Serial.printf("ph=%.2f (%.0f mV)  turb=%.2f NTU (%.0f mV)  tds=%.0f  temp=%.1f  orp=%.0f  faults=0x%02x  alert=%d\n",
                r.ph, r.ph_mv, r.ntu, r.turb_mv, r.tds, r.temp, r.orp, r.faults, alertRed);
}

#include "net.h"
#include "portal.h"

// ------------------------------------------------------------------ setup/loop

void setup() {
  Serial.begin(115200);
  delay(1000);
  pinMode(PIN_LED_R, OUTPUT); pinMode(PIN_LED_G, OUTPUT); pinMode(PIN_LED_B, OUTPUT);
  pinMode(PIN_BUZZER, OUTPUT);
  pinMode(PIN_PORTAL_BTN, INPUT_PULLUP);
  setLed(false, false);

  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);

  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL);
  lcd.init();
  lcd.backlight();
  lcd.setCursor(0, 0); lcd.print("StillWatch node ");
  lcd.setCursor(0, 1); lcd.print("v" FW_VERSION);

#if HAVE_TEMP
  tempSensor.begin();
#endif

  bootId = esp_random();
  bootReason = resetReasonText();
  qMutex = xSemaphoreCreateMutex();
  if (!flash.begin()) { storageFault = true; Serial.println("Flash storage failed: readings will not survive an outage."); }
  queue.begin();
  loadNetCfg();
  loadSettings();
  loadCalibration();
  Serial.printf("StillWatch node %s, device %s, boot %s, pH calibrated: %s, %u reading(s) waiting in flash\n",
                FW_VERSION, netCfg.dev, bootReason, phCal.valid ? "yes" : "no (use cal4 / cal7)", (unsigned)queue.count());

  // Hold BOOT for 3 seconds at power-up, or have no WiFi configured at all: open the setup portal.
  if (digitalRead(PIN_PORTAL_BTN) == LOW) {
    unsigned long t0 = millis();
    while (digitalRead(PIN_PORTAL_BTN) == LOW && millis() - t0 < 3000) delay(50);
    if (millis() - t0 >= 3000) portalRequested = true;
  }
  if (!netCfg.ssid[0]) portalRequested = true;

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  xTaskCreate(netTask, "net", 12288, nullptr, 1, nullptr);
}

void loop() {
  pollSerial();
  unsigned long now = millis();

  if (now - lastRead >= settings.read_ms || !haveReading) {
    lastRead = now;
    takeReading();
  }
  if (haveReading && (!everStored || now - lastStore >= settings.send_ms)) {
    lastStore = now;
    everStored = true;
    Rec r = latest;
    qLock();
    bool ok = queue.push(r);
    qUnlock();
    storageFault = !ok;
  }
  if (alertRed && now - lastBeep >= 5000) {
    lastBeep = now;
    tone(PIN_BUZZER, 2000, 300);
  }
  // Offline for half a day with no way back: a restart is cheap insurance against a wedged WiFi stack.
  if (netState == NET_OK) lastOkMs = now;
  else if (now - lastOkMs > 12UL * 3600UL * 1000UL && !portalActive) ESP.restart();
  delay(20);
}
