// Pure logic for the water node: no Arduino, no Wi-Fi, no flash. Everything here compiles and is unit-tested on a
// normal computer (see firmware/tests). The sketch only wires these functions to real hardware.
#pragma once
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>

// ---------------------------------------------------------------- checksums

inline uint32_t crc32_update(uint32_t crc, const uint8_t *p, size_t n) {
  crc = ~crc;
  for (size_t i = 0; i < n; i++) {
    crc ^= p[i];
    for (int k = 0; k < 8; k++) crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
  }
  return ~crc;
}
inline uint32_t crc32(const void *p, size_t n) { return crc32_update(0, (const uint8_t *)p, n); }

// ---------------------------------------------------------------- sampling

// Mean of the middle of `n` ADC samples after dropping the `trim` lowest and highest. One noisy conversion
// (common with WiFi on) then cannot move the result. Sorts `a` in place. `spread` = max - min of what is kept.
inline float trimmedMean(uint16_t *a, int n, int trim, float *spread = nullptr) {
  for (int i = 1; i < n; i++) {                 // insertion sort: n is small, no library needed
    uint16_t v = a[i];
    int j = i - 1;
    while (j >= 0 && a[j] > v) { a[j + 1] = a[j]; j--; }
    a[j + 1] = v;
  }
  if (trim * 2 >= n) trim = 0;
  long sum = 0;
  for (int i = trim; i < n - trim; i++) sum += a[i];
  if (spread) *spread = (float)(a[n - trim - 1] - a[trim]);
  return (float)sum / (float)(n - 2 * trim);
}

// A sensor line that is unplugged, shorted or floating shows up as a reading stuck at a rail or one that jumps
// around. Either way the value must not be reported as water.
inline bool sensorFault(float mv, float spread, float loMv, float hiMv, float maxSpreadMv) {
  return isnan(mv) || mv < loMv || mv > hiMv || spread > maxSpreadMv;
}

// DS18B20: -127 = not connected, 85 = the power-on value (the conversion never ran).
inline bool tempValid(float c) { return !isnan(c) && c > -55.0f && c < 125.0f && c != 85.0f && c != -127.0f; }

// ---------------------------------------------------------------- conversions

struct PhCal {
  bool valid = false;
  float slope = 0, offset = 0;   // pH = slope * mV + offset, at the temperature of the buffers
  float calTempC = NAN;          // NAN = calibration did not record a temperature: no compensation
};

inline PhCal phCalFromPoints(float mv4, float mv7, float bufferTempC = NAN) {
  PhCal c;
  if (isnan(mv4) || isnan(mv7) || fabsf(mv7 - mv4) < 20.0f) return c;
  c.slope = 3.0f / (mv7 - mv4);
  c.offset = 7.0f - c.slope * mv7;
  c.calTempC = tempValid(bufferTempC) ? bufferTempC : NAN;
  c.valid = true;
  return c;
}

// Same formula as app/calibration.py ph_from_mv: the electrode's slope scales with absolute temperature and
// pivots on pH 7 (where its output does not depend on temperature).
inline float phFromMv(float mv, const PhCal &c, float tempC = NAN) {
  if (!c.valid) return NAN;
  float ph;
  if (isnan(c.calTempC) || !tempValid(tempC)) {
    ph = c.slope * mv + c.offset;
  } else {
    float mv7 = (7.0f - c.offset) / c.slope;
    ph = 7.0f + c.slope * (mv - mv7) * (c.calTempC + 273.15f) / (tempC + 273.15f);
  }
  return ph < 0 ? 0 : (ph > 14 ? 14 : ph);
}

// Common datasheet curve for hobby turbidity modules (clear water about 4.2 V). Replace by calibrating
// against turbidity standards: the raw mV is always uploaded, so this can be redone on the server.
inline float ntuFromMv(float mv) {
  float v = mv / 1000.0f;
  if (v >= 4.2f) return 0.0f;
  if (v < 2.5f) return 3000.0f;
  float ntu = -1120.4f * v * v + 5742.3f * v - 4352.9f;
  return ntu < 0 ? 0 : ntu;
}

// Common datasheet polynomial for hobby TDS modules, with the usual 2 %/degree temperature compensation.
inline float tdsFromMv(float mv, float tempC = NAN) {
  float v = mv / 1000.0f;
  if (tempValid(tempC)) v /= (1.0f + 0.02f * (tempC - 25.0f));
  float tds = (133.42f * v * v * v - 255.86f * v * v + 857.39f * v) * 0.5f;
  return tds < 0 ? 0 : tds;
}

// Analog ORP module (DFRobot-style: 2.5 V at 0 mV). `offsetMv` is set from a known ORP standard.
inline float orpFromMv(float mv, float zeroMv, float gain, float offsetMv) {
  return (zeroMv - mv) / gain - offsetMv;
}

// ---------------------------------------------------------------- the stored record

enum Fault : uint8_t { F_PH = 1, F_TURB = 2, F_TDS = 4, F_TEMP = 8, F_ORP = 16, F_STORAGE = 32, F_CLOCK = 64 };
static const char *const FAULT_NAMES[7] = {"ph", "turbidity", "tds", "temp", "orp", "storage", "clock"};  // = app/schema.py FAULTS

#pragma pack(push, 1)
struct Rec {                 // one reading, as kept in the flash queue
  uint32_t magic;            // RECORD_MAGIC
  uint32_t ts;               // unix seconds; 0 = the clock was not synced when it was taken
  uint32_t boot_id;          // random per boot: lets a later clock sync repair ts=0 records of THIS boot
  uint32_t boot_s;           // seconds since boot when it was taken
  float ph, ph_mv, ntu, turb_mv, tds, tds_mv, temp, orp, orp_mv;   // NAN = not available
  uint8_t faults;            // Fault bits
  uint8_t pad[7];            // room to grow without moving crc: keep the size at 64
  uint32_t crc;              // crc32 of every byte before this field
};
#pragma pack(pop)
static_assert(sizeof(Rec) == 64, "Rec layout is part of the on-flash format");
#define RECORD_MAGIC 0x31525753u   // "SWR1"

inline void recInit(Rec &r) {
  memset(&r, 0, sizeof r);
  r.ph = r.ph_mv = r.ntu = r.turb_mv = r.tds = r.tds_mv = r.temp = r.orp = r.orp_mv = NAN;
}
inline void recSeal(Rec &r) { r.magic = RECORD_MAGIC; r.crc = crc32(&r, sizeof r - sizeof r.crc); }
inline bool recOk(const Rec &r) { return r.magic == RECORD_MAGIC && r.crc == crc32(&r, sizeof r - sizeof r.crc); }

// ---------------------------------------------------------------- time

// Unix seconds <-> "2026-10-09T10:15:00Z" without any libc time zone machinery.
inline void isoFromEpoch(uint32_t t, char *out /* >= 21 bytes */) {
  uint32_t days = t / 86400, rem = t % 86400;
  int64_t z = (int64_t)days + 719468;                       // civil_from_days (H. Hinnant)
  int64_t era = (z >= 0 ? z : z - 146096) / 146097;
  uint32_t doe = (uint32_t)(z - era * 146097);
  uint32_t yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
  int64_t y = (int64_t)yoe + era * 400;
  uint32_t doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
  uint32_t mp = (5 * doy + 2) / 153;
  uint32_t d = doy - (153 * mp + 2) / 5 + 1;
  uint32_t m = mp < 10 ? mp + 3 : mp - 9;
  if (m <= 2) y++;
  char tmp[48];                                              // roomy, so the compiler can see it cannot truncate
  snprintf(tmp, sizeof tmp, "%04d-%02u-%02uT%02u:%02u:%02uZ", (int)y, (unsigned)m, (unsigned)d,
           (unsigned)(rem / 3600), (unsigned)(rem % 3600 / 60), (unsigned)(rem % 60));
  memcpy(out, tmp, 20);
  out[20] = 0;
}

inline bool epochFromIso(const char *s, uint32_t *out) {
  int Y, M, D, h, mi, sec;
  if (!s || sscanf(s, "%4d-%2d-%2dT%2d:%2d:%2d", &Y, &M, &D, &h, &mi, &sec) != 6) return false;
  if (Y < 2020 || M < 1 || M > 12 || D < 1 || D > 31 || h > 23 || mi > 59 || sec > 60) return false;
  int64_t y = Y - (M <= 2), era = (y >= 0 ? y : y - 399) / 400;
  uint32_t yoe = (uint32_t)(y - era * 400);
  uint32_t doy = (153 * (M + (M > 2 ? -3 : 9)) + 2) / 5 + D - 1;
  uint32_t doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
  int64_t days = era * 146097 + (int64_t)doe - 719468;
  int64_t t = days * 86400 + h * 3600 + mi * 60 + sec;
  if (t < 0 || t > 4294967295LL) return false;
  *out = (uint32_t)t;
  return true;
}

// A record taken before the clock synced is repaired once it has: ts = now - (seconds since it was taken).
inline uint32_t repairedTs(const Rec &r, uint32_t bootId, uint32_t nowEpoch, uint32_t nowBootS) {
  if (r.ts) return r.ts;
  if (!nowEpoch || r.boot_id != bootId || r.boot_s > nowBootS) return 0;
  return nowEpoch - (nowBootS - r.boot_s);
}

// ---------------------------------------------------------------- JSON out (hand-built, no library)

struct Out {              // bounded writer: never overruns, remembers if it ran out of room
  char *buf; size_t cap, n; bool full;
  Out(char *b, size_t c) : buf(b), cap(c), n(0), full(false) { if (c) b[0] = 0; }
  void put(const char *fmt, ...) __attribute__((format(printf, 2, 3))) {
    if (full) return;
    va_list ap; va_start(ap, fmt);
    int k = vsnprintf(buf + n, cap - n, fmt, ap);
    va_end(ap);
    if (k < 0 || (size_t)k >= cap - n) { full = true; buf[n] = 0; } else n += (size_t)k;
  }
};

// The fields of one reading, each leading with a comma. A NAN (sensor absent or faulty) is simply left out.
inline void putReadingFields(Out &o, const Rec &r, uint32_t ts) {
  if (ts) { char t[21]; isoFromEpoch(ts, t); o.put(",\"ts\":\"%s\"", t); }
  if (!isnan(r.ph))      o.put(",\"ph\":%.2f", r.ph);
  if (!isnan(r.ph_mv))   o.put(",\"ph_raw_mv\":%.0f", r.ph_mv);
  if (!isnan(r.ntu))     o.put(",\"turbidity_ntu\":%.2f", r.ntu);
  if (!isnan(r.turb_mv)) o.put(",\"turbidity_raw_mv\":%.0f", r.turb_mv);
  if (!isnan(r.tds))     o.put(",\"tds_ppm\":%.0f", r.tds);
  if (!isnan(r.tds_mv))  o.put(",\"tds_raw_mv\":%.0f", r.tds_mv);
  if (!isnan(r.temp))    o.put(",\"temp_c\":%.2f", r.temp);
  if (!isnan(r.orp))     o.put(",\"orp_mv\":%.0f", r.orp);
  if (!isnan(r.orp_mv))  o.put(",\"orp_raw_mv\":%.0f", r.orp_mv);
}

// One reading as a JSON object, for the batch upload: {"ts":"...","ph":7.2,...}
inline void putReadingObject(Out &o, const Rec &r, uint32_t ts) {
  o.put("{");
  size_t start = o.n;
  putReadingFields(o, r, ts);
  if (!o.full && o.n > start && o.buf[start] == ',') {      // the fields each lead with a comma: drop the first
    memmove(o.buf + start, o.buf + start + 1, o.n - start);
    o.n--;
  }
  o.put("}");
}

struct Telemetry { int rssi; uint32_t uptime_s; int queued; const char *boot; uint8_t faults; const char *fw; };

// ,"rssi":-60,"uptime_s":..., "faults":["ph"]  (each field leads with a comma)
inline void putTelemetry(Out &o, const Telemetry &t) {
  if (t.fw) o.put(",\"fw\":\"%s\"", t.fw);
  if (t.rssi < 0 && t.rssi >= -127) o.put(",\"rssi\":%d", t.rssi);
  o.put(",\"uptime_s\":%lu,\"queued\":%d", (unsigned long)t.uptime_s, t.queued);
  if (t.boot && t.boot[0]) o.put(",\"boot\":\"%s\"", t.boot);
  o.put(",\"faults\":[");
  bool first = true;
  for (int i = 0; i < 7; i++)
    if (t.faults & (1u << i)) { o.put("%s\"%s\"", first ? "" : ",", FAULT_NAMES[i]); first = false; }
  o.put("]");
}

// The three request bodies the node sends. net.h and the host tests both use these, so what the tests check is
// exactly what goes on the wire.
inline void putSingleBody(Out &o, const char *deviceId, const Rec &r, uint32_t ts, const Telemetry &t) {
  o.put("{\"schema\":1,\"device_id\":\"%s\"", deviceId);
  putReadingFields(o, r, ts);
  putTelemetry(o, t);
  o.put("}");
}

// Readings with no usable timestamp are left out (a backlog needs real times). Returns how many went in.
inline int putBatchBody(Out &o, const char *deviceId, const Telemetry &t, const Rec *recs, const uint32_t *ts, int n) {
  o.put("{\"schema\":1,\"device_id\":\"%s\"", deviceId);
  putTelemetry(o, t);
  o.put(",\"readings\":[");
  int count = 0;
  for (int i = 0; i < n; i++) {
    if (!ts[i]) continue;
    if (count) o.put(",");
    putReadingObject(o, recs[i], ts[i]);
    count++;
  }
  o.put("]}");
  return count;
}

inline void putCheckinBody(Out &o, const char *deviceId, const Telemetry &t) {
  o.put("{\"schema\":1,\"device_id\":\"%s\"", deviceId);
  putTelemetry(o, t);
  o.put("}");
}

// ---------------------------------------------------------------- JSON in (just enough for the check-in reply)

// Finds "key": and returns a pointer just past the colon and any spaces, or null. `from` lets a caller search
// inside a nested object that starts at that position.
inline const char *jsonFind(const char *body, const char *key, const char *from = nullptr) {
  char pat[48];
  snprintf(pat, sizeof pat, "\"%s\"", key);
  const char *p = strstr(from ? from : body, pat);
  if (!p) return nullptr;
  p += strlen(pat);
  while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r') p++;
  if (*p != ':') return nullptr;
  p++;
  while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r') p++;
  return p;
}
inline bool jsonNum(const char *body, const char *key, float *out, const char *from = nullptr) {
  const char *p = jsonFind(body, key, from);
  if (!p) return false;
  char *end;
  float v = strtof(p, &end);
  if (end == p) return false;
  *out = v;
  return true;
}
inline bool jsonStr(const char *body, const char *key, char *out, size_t cap, const char *from = nullptr) {
  const char *p = jsonFind(body, key, from);
  if (!p || *p != '"') return false;
  p++;
  size_t i = 0;
  while (*p && *p != '"' && i + 1 < cap) out[i++] = *p++;
  if (*p != '"') return false;
  out[i] = 0;
  return true;
}

// ---------------------------------------------------------------- settings from the server

struct Settings {
  uint32_t read_ms, send_ms;
  float ph_min, ph_max, turb_max;
};

// Takes what the server sent only if it is sane; anything else keeps the current value. The server cannot
// push the node into a state that floods it or silences its local alarm.
inline bool applyConfig(Settings &s, const char *body) {
  const char *cfg = jsonFind(body, "config");
  if (!cfg) return false;
  bool changed = false;
  float v;
  if (jsonNum(body, "read_period_s", &v, cfg) && v >= 2 && v <= 3600 && (uint32_t)(v * 1000) != s.read_ms) { s.read_ms = (uint32_t)(v * 1000); changed = true; }
  if (jsonNum(body, "send_period_s", &v, cfg) && v >= 10 && v <= 86400 && (uint32_t)(v * 1000) != s.send_ms) { s.send_ms = (uint32_t)(v * 1000); changed = true; }
  float lo = s.ph_min, hi = s.ph_max;
  if (jsonNum(body, "ph_min", &v, cfg)) lo = v;
  if (jsonNum(body, "ph_max", &v, cfg)) hi = v;
  if (lo >= 0 && hi <= 14 && lo < hi && (lo != s.ph_min || hi != s.ph_max)) { s.ph_min = lo; s.ph_max = hi; changed = true; }
  if (jsonNum(body, "turb_max_ntu", &v, cfg) && v > 0 && v <= 3000 && v != s.turb_max) { s.turb_max = v; changed = true; }
  return changed;
}

struct UpdateOffer { char version[33]; char sha256[65]; char path[96]; uint32_t size; };

inline bool parseUpdate(const char *body, UpdateOffer &u) {
  const char *p = jsonFind(body, "update");
  if (!p || *p != '{') return false;                       // "update": null  -> nothing to do
  float size;
  if (!jsonStr(body, "version", u.version, sizeof u.version, p) || !jsonStr(body, "sha256", u.sha256, sizeof u.sha256, p) ||
      !jsonStr(body, "path", u.path, sizeof u.path, p) || !jsonNum(body, "size", &size, p))
    return false;
  if (strlen(u.sha256) != 64 || u.path[0] != '/' || size < 65536 || size > 4194304) return false;
  u.size = (uint32_t)size;
  return true;
}

inline bool hexEqualNoCase(const char *a, const char *b) {
  size_t n = strlen(a);
  if (n != strlen(b)) return false;
  for (size_t i = 0; i < n; i++) {
    char x = a[i], y = b[i];
    if (x >= 'A' && x <= 'F') x += 32;
    if (y >= 'A' && y <= 'F') y += 32;
    if (x != y) return false;
  }
  return true;
}

// Local safety rule: works with no network. Uses the limits last received from the server.
inline bool outsideLimits(const Rec &r, const Settings &s) {
  if (!isnan(r.ph) && (r.ph < s.ph_min || r.ph > s.ph_max)) return true;
  return !isnan(r.ntu) && r.ntu > s.turb_max;
}
