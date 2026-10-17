#pragma once
#include "Arduino.h"
#define WL_CONNECTED 3
#define WIFI_STA 1
#define WIFI_AP_STA 3
struct IPAddress { String toString() const { return String("192.168.4.1"); } };
struct WiFiClient {
  int available() { return 0; }
  size_t readBytes(uint8_t *, size_t) { return 0; }
  bool connected() { return false; }
};
struct WiFiClass {
  void mode(int) {}
  void begin(const char *, const char *) {}
  int status() { return 0; }
  int RSSI() { return -60; }
  void setAutoReconnect(bool) {}
  void disconnect() {}
  bool softAP(const char *, const char *) { return true; }
  bool softAPdisconnect(bool) { return true; }
  IPAddress softAPIP() { return IPAddress(); }
  void macAddress(uint8_t *m) { memset(m, 0, 6); }
};
extern WiFiClass WiFi;
