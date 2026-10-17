#pragma once
#include "Arduino.h"
struct Preferences {
  bool begin(const char *, bool = false) { return true; }
  void end() {} void clear() {}
  String getString(const char *, const char *d = "") { return String(d); }
  size_t putString(const char *, const String &) { return 1; }
  uint32_t getUInt(const char *, uint32_t d = 0) { return d; }
  size_t putUInt(const char *, uint32_t) { return 4; }
  float getFloat(const char *, float d = 0) { return d; }
  size_t putFloat(const char *, float) { return 4; }
};
