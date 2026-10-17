// Host-side tests for the pure firmware logic. Build and run:  sh firmware/tests/run.sh
#include <cassert>
#include <cstdio>
#include <map>
#include <string>
#include <vector>
#include "logic.h"
#include "queue.h"

static int failures = 0;
#define CHECK(c) do { if (!(c)) { printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #c); failures++; } } while (0)
#define NEAR(a, b, eps) CHECK(fabs((double)(a) - (double)(b)) <= (eps))

// ---- in-memory storage for the queue, with switches to simulate flash trouble
struct MemFs {
  std::map<std::string, std::vector<uint8_t>> f;
  bool failAppend = false;
  size_t size(const char *p) { auto it = f.find(p); return it == f.end() ? 0 : it->second.size(); }
  bool append(const char *p, const void *d, size_t n) {
    if (failAppend) return false;
    auto &v = f[p]; v.insert(v.end(), (const uint8_t *)d, (const uint8_t *)d + n); return true;
  }
  bool readAt(const char *p, size_t off, void *b, size_t n) {
    auto it = f.find(p); if (it == f.end() || off + n > it->second.size()) return false;
    memcpy(b, it->second.data() + off, n); return true;
  }
  bool replace(const char *p, const void *d, size_t n) { f[p].assign((const uint8_t *)d, (const uint8_t *)d + n); return true; }
  bool rename(const char *a, const char *b) { auto it = f.find(a); if (it == f.end()) return false; f[b] = it->second; f.erase(a); return true; }
  void remove(const char *p) { f.erase(p); }
};

static Rec mk(uint32_t ts, float ph = 7.2f) {
  Rec r; recInit(r); r.ts = ts; r.ph = ph; r.ntu = 1.5f; r.turb_mv = 4100; return r;
}

static void test_crc_and_record() {
  CHECK(crc32("123456789", 9) == 0xCBF43926u);          // the standard CRC-32 check value
  Rec r = mk(1000); recSeal(r); CHECK(recOk(r));
  r.ph = 9; CHECK(!recOk(r));                           // any change breaks the checksum
}

static void test_trimmed_mean_and_faults() {
  uint16_t a[10] = {100, 101, 99, 100, 102, 98, 100, 101, 3000, 0};   // two wild conversions
  float spread; float m = trimmedMean(a, 10, 2, &spread);
  NEAR(m, 100.0, 1.0); CHECK(spread <= 4);
  CHECK(sensorFault(NAN, 0, 20, 4900, 150));
  CHECK(sensorFault(5, 1, 20, 4900, 150));              // stuck at the bottom rail
  CHECK(sensorFault(3000, 400, 20, 4900, 150));         // jumping about: floating input
  CHECK(!sensorFault(2500, 3, 20, 4900, 150));
  CHECK(!tempValid(85.0f) && !tempValid(-127.0f) && !tempValid(NAN) && tempValid(29.5f));
}

static void test_conversions_match_the_server() {
  // The same numbers are asserted in tests/test_firmware_parity.py against app/calibration.py.
  PhCal c = phCalFromPoints(3000, 2500, 25);
  CHECK(c.valid); NEAR(c.slope, -0.006, 1e-6); NEAR(c.offset, 22.0, 1e-4);
  NEAR(phFromMv(2750, c, 25), 5.5, 1e-4);
  NEAR(phFromMv(2750, c, NAN), 5.5, 1e-4);              // no temperature -> uncompensated
  NEAR(phFromMv(2750, c, 45), 5.594295, 1e-4);          // warmer water: closer to 7 for the same mV
  NEAR(phFromMv(2500, c, 45), 7.0, 1e-4);               // pivot at pH 7
  CHECK(isnan(phFromMv(2750, phCalFromPoints(2500, 2510))));       // buffers too close together: no pH at all
  PhCal nt = phCalFromPoints(3000, 2500);               // calibration without a recorded temperature
  NEAR(phFromMv(2750, nt, 45), 5.5, 1e-4);
  NEAR(ntuFromMv(4300), 0.0, 1e-6);
  NEAR(tdsFromMv(1000, NAN), (133.42 - 255.86 + 857.39) * 0.5, 0.01);
  CHECK(tdsFromMv(1000, 35) < tdsFromMv(1000, NAN));    // warm water reads higher, compensation lowers it
  NEAR(orpFromMv(2500, 2500, 1.037, 0), 0, 1e-6);
  NEAR(orpFromMv(2000, 2500, 1.037, 10), 500 / 1.037 - 10, 1e-3);
}

static void test_time() {
  char s[21];
  isoFromEpoch(0, s); CHECK(!strcmp(s, "1970-01-01T00:00:00Z"));
  isoFromEpoch(1760000000, s); CHECK(!strcmp(s, "2025-10-09T08:53:20Z"));
  isoFromEpoch(1709164799, s); CHECK(!strcmp(s, "2024-02-28T23:59:59Z"));
  isoFromEpoch(1709164800, s); CHECK(!strcmp(s, "2024-02-29T00:00:00Z"));      // leap day
  for (uint32_t t : {1577836800u, 1709164800u, 1760000000u, 2000000000u, 4102444800u}) {
    char b[21]; uint32_t back; isoFromEpoch(t, b); CHECK(epochFromIso(b, &back) && back == t);
  }
  uint32_t t; CHECK(!epochFromIso("garbage", &t)); CHECK(!epochFromIso("1970-01-01T00:00:00Z", &t));   // an unset clock is rejected
   CHECK(!epochFromIso("2026-13-01T00:00:00Z", &t));
  Rec r = mk(0); r.boot_id = 77; r.boot_s = 100;
  CHECK(repairedTs(r, 77, 1760000000, 160) == 1760000000 - 60);
  CHECK(repairedTs(r, 78, 1760000000, 160) == 0);       // from another boot: cannot be placed
  CHECK(repairedTs(r, 77, 0, 160) == 0);                // clock still not synced
  r.ts = 555; CHECK(repairedTs(r, 1, 1, 1) == 555);
}

static void test_json_out() {
  char buf[400]; Out o(buf, sizeof buf);
  Rec r = mk(1760000000); r.ph_mv = 2480; r.temp = 29.5f;
  o.put("{\"x\":1"); putReadingFields(o, r, r.ts); o.put("}");
  CHECK(!o.full);
  CHECK(strstr(buf, "\"ts\":\"2025-10-09T08:53:20Z\"") && strstr(buf, "\"ph\":7.20") && strstr(buf, "\"temp_c\":29.50"));
  CHECK(!strstr(buf, "tds_ppm") && !strstr(buf, "nan"));  // absent sensors are left out entirely
  char small[20]; Out s(small, sizeof small); putReadingFields(s, r, r.ts);
  CHECK(s.full && strlen(small) < sizeof small);          // never overruns
  char t[200]; Out o2(t, sizeof t);
  Telemetry tel = {-61, 900, 5, "power_on", (uint8_t)(F_PH | F_ORP), "0.5.0"};
  putTelemetry(o2, tel);
  CHECK(!strcmp(t, ",\"fw\":\"0.5.0\",\"rssi\":-61,\"uptime_s\":900,\"queued\":5,\"boot\":\"power_on\",\"faults\":[\"ph\",\"orp\"]"));
}

static void test_json_in_and_config() {
  const char *reply = "{\"config\": {\"ph_max\": 8.5, \"ph_min\": 6.5, \"read_period_s\": 20, \"send_period_s\": 60,\n"
                      "  \"turb_max_ntu\": 5.0}, \"ok\": true, \"server_time\": \"2026-10-17T08:00:00Z\", \"update\": null}";
  Settings s = {10000, 60000, 6.0f, 9.0f, 4.0f};
  CHECK(applyConfig(s, reply));
  CHECK(s.read_ms == 20000 && s.send_ms == 60000);
  NEAR(s.ph_min, 6.5, 1e-6); NEAR(s.ph_max, 8.5, 1e-6); NEAR(s.turb_max, 5.0, 1e-6);
  CHECK(!applyConfig(s, reply));                        // nothing changed the second time
  UpdateOffer u; CHECK(!parseUpdate(reply, u));
  char ts[32]; CHECK(jsonStr(reply, "server_time", ts, sizeof ts) && !strcmp(ts, "2026-10-17T08:00:00Z"));
  // hostile or broken values are ignored
  Settings k = {10000, 60000, 6.5f, 8.5f, 5.0f};
  CHECK(!applyConfig(k, "{\"config\":{\"read_period_s\":0.1,\"send_period_s\":5,\"ph_min\":9,\"ph_max\":8,\"turb_max_ntu\":-1}}"));
  CHECK(k.read_ms == 10000 && k.send_ms == 60000 && k.ph_min == 6.5f && k.turb_max == 5.0f);
  CHECK(!applyConfig(k, "not json"));
  std::string good = "{\"update\":{\"path\":\"/api/firmware/0.6.0.bin\",\"sha256\":\"" + std::string(64, 'a') +
                     "\",\"size\":1048576,\"version\":\"0.6.0\"},\"config\":{}}";
  CHECK(parseUpdate(good.c_str(), u) && !strcmp(u.version, "0.6.0") && u.size == 1048576 && u.path[0] == '/');
  std::string bad = good; bad.replace(bad.find("1048576"), 7, "10");        // implausible size
  CHECK(!parseUpdate(bad.c_str(), u));
  CHECK(hexEqualNoCase("ABCdef01", "abcDEF01") && !hexEqualNoCase("abc", "abd") && !hexEqualNoCase("ab", "abc"));
  Rec r = mk(1); r.ph = 9.0f; CHECK(outsideLimits(r, s));
  r = mk(1); r.ntu = 6; CHECK(outsideLimits(r, s));
  r = mk(1); r.ph = NAN; r.ntu = 1; CHECK(!outsideLimits(r, s));
}

typedef PersistentQueue<MemFs, 50, 10> Q;

static void test_queue_basics_and_reboot() {
  MemFs fs; Q q(fs); q.begin();
  for (uint32_t i = 1; i <= 7; i++) { Rec r = mk(i); CHECK(q.push(r)); }
  CHECK(q.count() == 7);
  Rec out[4]; int n = q.peek(out, 4);
  CHECK(n == 4 && out[0].ts == 1 && out[3].ts == 4);
  CHECK(q.count() == 7);                                // peek removes nothing
  q.pop(n); CHECK(q.count() == 3);
  Q again(fs); again.begin();                           // "reboot"
  CHECK(again.count() == 3);
  n = again.peek(out, 4); CHECK(n == 3 && out[0].ts == 5);
  again.pop(n); CHECK(again.count() == 0 && fs.size("/q.log") == 0);   // emptied queue leaves no files
}

static void test_queue_capacity_compaction_and_order() {
  MemFs fs; Q q(fs); q.begin();
  for (uint32_t i = 1; i <= 120; i++) { Rec r = mk(i); q.push(r); }
  CHECK(q.count() == 50 && q.dropped == 70);            // capacity 50: oldest dropped
  Rec out[8]; int n = q.peek(out, 8);
  CHECK(n == 8 && out[0].ts == 71);
  CHECK(fs.size("/q.log") / sizeof(Rec) < 80);          // compaction kept the file from growing with every push
  uint32_t expect = 71; uint32_t seen = 0;
  while ((n = q.peek(out, 8)) > 0) { for (int i = 0; i < n; i++) CHECK(out[i].ts == expect++); seen += n; q.pop(n); }
  CHECK(seen == 50 && expect == 121);
}

static void test_queue_torn_tail_and_corruption() {
  MemFs fs; { Q q(fs); q.begin(); for (uint32_t i = 1; i <= 5; i++) { Rec r = mk(i); q.push(r); } }
  fs.f["/q.log"].resize(fs.f["/q.log"].size() - 20);    // power cut part-way through the last append
  Q q(fs); q.begin();
  CHECK(q.count() == 4);
  Rec r = mk(6); CHECK(q.push(r));                      // and appending still lines up afterwards
  Rec out[10]; int n = q.peek(out, 10); CHECK(n == 5 && out[4].ts == 6);

  fs.f["/q.log"][sizeof(Rec) * 1 + 9] ^= 0xFF;          // flip a byte inside record #2
  Q c(fs); c.begin();
  n = c.peek(out, 10);
  CHECK(n == 1 && out[0].ts == 1);                      // good one first
  c.pop(n);
  n = c.peek(out, 10);
  CHECK(n == 3 && out[0].ts == 3 && c.dropped == 1);    // the corrupt one is stepped over, not retried forever
}

static void test_queue_storage_failure_and_crash_safety() {
  MemFs fs; Q q(fs); q.begin();
  Rec r = mk(1); fs.failAppend = true;
  CHECK(!q.push(r)); CHECK(q.count() == 0);             // reports failure instead of pretending
  fs.failAppend = false;
  for (uint32_t i = 1; i <= 9; i++) { Rec x = mk(i); q.push(x); }
  Rec out[9]; int n = q.peek(out, 9); q.pop(n - 1);     // 1 left, head persisted
  // Simulate a crash after the head file said "9 consumed" but nothing else: a new boot sees a consistent queue
  Q again(fs); again.begin();
  CHECK(again.count() == 1);
  // meta file garbage -> start from the beginning (resend) rather than lose or misread anything
  MemFs g; { Q a(g); a.begin(); for (uint32_t i = 1; i <= 4; i++) { Rec x = mk(i); a.push(x); } Rec o[2]; a.pop(a.peek(o, 2)); }
  g.f["/q.head"] = {1, 2, 3};
  Q b(g); b.begin(); CHECK(b.count() == 4);
}

// `test_logic dump` prints the request bodies the node would send; `test_logic parse` reads a check-in reply
// on stdin and prints what the node would do with it. tests/test_firmware_contract.py feeds both to the backend.
static int dump(uint32_t base) {
  static char buf[8192];
  Telemetry tel = {-63, 4000, 3, "power_on", (uint8_t)F_ORP, "0.5.0"};
  Rec a = mk(base, 7.31f); a.ph_mv = 2480; a.turb_mv = 3100; a.ntu = 3.4f; a.tds = 312; a.tds_mv = 800; a.temp = 29.5f; a.orp = 410; a.orp_mv = 1500;
  Rec b = mk(base + 60); b.ph = NAN;                                   // pH not calibrated yet: left out
  Rec c = mk(0);                                                        // no timestamp: left out of a batch
  Rec recs[3] = {a, b, c}; uint32_t ts[3] = {a.ts, b.ts, 0};
  { Out o(buf, sizeof buf); putSingleBody(o, "tank-fw-01", a, a.ts, tel); printf("SINGLE:%s\n", buf); }
  { Out o(buf, sizeof buf); int n = putBatchBody(o, "tank-fw-01", tel, recs, ts, 3); printf("BATCH%d:%s\n", n, buf); }
  { Out o(buf, sizeof buf); putCheckinBody(o, "tank-fw-01", tel); printf("CHECKIN:%s\n", buf); }
  return 0;
}

static int parse() {
  static char body[4096];
  size_t n = fread(body, 1, sizeof body - 1, stdin);
  body[n] = 0;
  Settings s = {10000, 60000, 6.0f, 9.0f, 4.0f};
  applyConfig(s, body);
  UpdateOffer u; bool has = parseUpdate(body, u);
  printf("read_ms=%u send_ms=%u ph_min=%.2f ph_max=%.2f turb=%.2f update=%s sha=%s size=%u path=%s\n", s.read_ms, s.send_ms,
         s.ph_min, s.ph_max, s.turb_max, has ? u.version : "none", has ? u.sha256 : "-", has ? u.size : 0, has ? u.path : "-");
  return 0;
}

int main(int argc, char **argv) {
  if (argc > 1 && !strcmp(argv[1], "dump")) return dump(argc > 2 ? (uint32_t)strtoul(argv[2], nullptr, 10) : 1760000000u);
  if (argc > 1 && !strcmp(argv[1], "parse")) return parse();
  test_crc_and_record();
  test_trimmed_mean_and_faults();
  test_conversions_match_the_server();
  test_time();
  test_json_out();
  test_json_in_and_config();
  test_queue_basics_and_reboot();
  test_queue_capacity_compaction_and_order();
  test_queue_torn_tail_and_corruption();
  test_queue_storage_failure_and_crash_safety();
  if (failures) { printf("%d check(s) failed\n", failures); return 1; }
  printf("firmware logic: all checks passed\n");
  return 0;
}
