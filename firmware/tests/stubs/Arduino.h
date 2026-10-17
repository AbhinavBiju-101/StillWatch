// Minimal stand-ins for the Arduino-ESP32 APIs the sketch uses. They exist ONLY so the sketch can be
// compile-checked on a normal computer (see ../compile_check.sh). They do nothing; signatures follow
// arduino-esp32 3.x as far as the sketch uses them.
#pragma once
#include <stdint.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <stdlib.h>
#include <ctype.h>
#include <math.h>
#include <string>
#include <functional>
#include <sys/time.h>
#include <time.h>

#define LOW 0
#define HIGH 1
#define OUTPUT 1
#define INPUT_PULLUP 2
#define ADC_11db 3
#define F(x) x
#define pdMS_TO_TICKS(x) (x)
#define portMAX_DELAY 0xFFFFFFFFu
typedef void *SemaphoreHandle_t;
inline SemaphoreHandle_t xSemaphoreCreateMutex() { return nullptr; }
inline void xSemaphoreTake(SemaphoreHandle_t, uint32_t) {}
inline void xSemaphoreGive(SemaphoreHandle_t) {}
inline void vTaskDelay(uint32_t) {}
inline int xTaskCreate(void (*)(void *), const char *, uint32_t, void *, int, void *) { return 1; }
inline unsigned long millis() { return 0; }
inline void delay(unsigned long) {}
inline void delayMicroseconds(unsigned int) {}
inline void pinMode(int, int) {}
inline void digitalWrite(int, int) {}
inline int digitalRead(int) { return 1; }
inline void analogReadResolution(int) {}
inline void analogSetAttenuation(int) {}
inline uint32_t analogReadMilliVolts(int) { return 0; }
inline void tone(int, unsigned, unsigned long) {}
inline uint32_t esp_random() { return 4; }
inline size_t strlcpy(char *d, const char *s, size_t n) { if (n) { strncpy(d, s, n - 1); d[n - 1] = 0; } return strlen(s); }
inline size_t strlcat(char *d, const char *s, size_t n) { size_t l = strlen(d); if (l + 1 < n) strncat(d, s, n - l - 1); return l + strlen(s); }
inline void configTime(long, int, const char *, const char * = nullptr) {}

class String {
  std::string s;
 public:
  String() {}
  String(const char *c) : s(c) {}
  String(float v, int d) { char b[40]; snprintf(b, sizeof b, "%.*f", d, v); s = b; }
  const char *c_str() const { return s.c_str(); }
  size_t length() const { return s.size(); }
  void trim() { while (!s.empty() && isspace((unsigned char)s.back())) s.pop_back(); size_t i = 0; while (i < s.size() && isspace((unsigned char)s[i])) i++; s.erase(0, i); }
  bool endsWith(const char *x) const { size_t n = strlen(x); return s.size() >= n && s.compare(s.size() - n, n, x) == 0; }
  bool startsWith(const char *x) const { return s.rfind(x, 0) == 0; }
  void remove(unsigned idx) { s.erase(idx); }
  char operator[](size_t i) const { return s[i]; }
  String &operator+=(const String &o) { s += o.s; return *this; }
  String &operator+=(const char *o) { s += o; return *this; }
  String &operator+=(char c) { s += c; return *this; }
  friend String operator+(String a, const String &b) { a += b; return a; }
  friend String operator+(const char *a, const String &b) { String r(a); r += b; return r; }
  friend String operator+(String a, const char *b) { a += b; return a; }
};

struct SerialClass {
  void begin(int) {}
  int available() { return 0; }
  int read() { return -1; }
  void println(const char *) {}
  void printf(const char *, ...) __attribute__((format(printf, 2, 3))) {}
};
extern SerialClass Serial;
struct EspClass { void restart() {} };
extern EspClass ESP;
