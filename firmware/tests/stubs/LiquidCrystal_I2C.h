#pragma once
#include "Arduino.h"
struct LiquidCrystal_I2C {
  LiquidCrystal_I2C(int, int, int) {}
  void init() {} void backlight() {} void setCursor(int, int) {}
  void print(const char *) {}
  void printf(const char *, ...) __attribute__((format(printf, 2, 3))) {}
};
