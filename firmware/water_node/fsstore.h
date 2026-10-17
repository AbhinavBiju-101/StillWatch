// LittleFS adapter for PersistentQueue (see queue.h for the interface it implements).
#pragma once
#include <LittleFS.h>

struct FlashStore {
  bool ready = false;
  bool begin() { ready = LittleFS.begin(true); return ready; }   // true = format on first use

  size_t size(const char *path) {
    if (!ready || !LittleFS.exists(path)) return 0;
    File f = LittleFS.open(path, "r");
    if (!f) return 0;
    size_t n = f.size();
    f.close();
    return n;
  }
  bool append(const char *path, const void *data, size_t n) {
    if (!ready) return false;
    File f = LittleFS.open(path, "a");
    if (!f) return false;
    size_t w = f.write((const uint8_t *)data, n);
    f.close();
    return w == n;
  }
  bool readAt(const char *path, size_t offset, void *buf, size_t n) {
    if (!ready) return false;
    File f = LittleFS.open(path, "r");
    if (!f) return false;
    bool ok = f.seek(offset) && f.read((uint8_t *)buf, n) == n;
    f.close();
    return ok;
  }
  bool replace(const char *path, const void *data, size_t n) {
    if (!ready) return false;
    File f = LittleFS.open(path, "w");
    if (!f) return false;
    size_t w = f.write((const uint8_t *)data, n);
    f.close();
    return w == n;
  }
  bool rename(const char *from, const char *to) {
    if (!ready) return false;
    LittleFS.remove(to);
    return LittleFS.rename(from, to);
  }
  void remove(const char *path) { if (ready) LittleFS.remove(path); }
};
