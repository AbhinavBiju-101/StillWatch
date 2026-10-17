#pragma once
#include "Arduino.h"
struct File {
  explicit operator bool() const { return true; }
  size_t size() { return 0; } void close() {}
  size_t write(const uint8_t *, size_t n) { return n; }
  size_t read(uint8_t *, size_t) { return 0; }
  bool seek(size_t) { return true; }
};
struct LittleFSClass {
  bool begin(bool = false) { return true; }
  bool exists(const char *) { return false; }
  File open(const char *, const char * = "r") { return File(); }
  bool remove(const char *) { return true; }
  bool rename(const char *, const char *) { return true; }
};
extern LittleFSClass LittleFS;
