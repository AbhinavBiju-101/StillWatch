#pragma once
#include "WiFiClientSecure.h"
struct HTTPClient {
  bool begin(WiFiClient &, const char *) { return true; }
  void setTimeout(int) {}
  void addHeader(const char *, const char *) {}
  int POST(uint8_t *, size_t) { return 200; }
  int GET() { return 200; }
  String getString() { return String(); }
  int getSize() { return 0; }
  WiFiClient *getStreamPtr() { static WiFiClient c; return &c; }
  bool connected() { return false; }
  void end() {}
};
