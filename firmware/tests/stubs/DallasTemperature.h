#pragma once
#include "OneWire.h"
struct DallasTemperature {
  DallasTemperature(OneWire *) {}
  void begin() {} void requestTemperatures() {}
  float getTempCByIndex(int) { return 25.0f; }
};
