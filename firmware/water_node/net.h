// Networking for the node: WiFi, uploads, check-in, over-the-air updates. Included by water_node.ino after the
// globals it uses (netCfg, settings, queue, ...). Runs in its own task so sensing and local alarms never wait on it.
#pragma once

void runPortal(int seconds);                // defined in portal.h, which is included after this file

static char batchBuf[BATCH_MAX * 240 + 512];
static char respBuf[1024];
static bool serverHasBatch = true;          // false after a 404: an older backend; fall back to one POST per reading
static bool serverHasCheckin = true;

Telemetry telemetryNow() {
  qLock(); int queued = (int)queue.count(); qUnlock();
  uint8_t f = currentFaults | (storageFault ? F_STORAGE : 0);
  return {WiFi.status() == WL_CONNECTED ? WiFi.RSSI() : 0, (uint32_t)(millis() / 1000), queued, bootReason, f, FW_VERSION};
}

// Opens `url` on `http` with the right transport. The clients must outlive the request: the caller owns them.
void beginHttp(HTTPClient &http, WiFiClientSecure &secure, WiFiClient &plain, const char *url) {
  if (strncmp(url, "https", 5) == 0) {
#if TLS_INSECURE
    secure.setInsecure();
#else
    secure.setCACert(ROOT_CA_PEM);
#endif
    http.begin(secure, url);
  } else {
    http.begin(plain, url);
  }
  http.setTimeout(HTTP_TIMEOUT_MS);
  http.addHeader("X-Device-Token", netCfg.token);
}

// POST JSON to `path`. Returns the HTTP status (negative = could not reach the server). A reply body, if wanted,
// is copied into `resp` (truncated to cap-1 bytes).
int httpPost(const char *path, const char *body, char *resp = nullptr, size_t cap = 0) {
  char url[200];
  snprintf(url, sizeof url, "%s%s", netCfg.url, path);
  HTTPClient http;
  WiFiClientSecure secure;
  WiFiClient plain;
  beginHttp(http, secure, plain, url);
  http.addHeader("Content-Type", "application/json");
  int code = http.POST((uint8_t *)body, strlen(body));
  if (resp && cap) {
    String s = code > 0 ? http.getString() : String();
    strlcpy(resp, s.c_str(), cap);
  }
  http.end();
  return code;
}

bool connectWifi() {
  WiFi.begin(netCfg.ssid, netCfg.pass);
  for (int i = 0; i < 30 && WiFi.status() != WL_CONNECTED; i++) vTaskDelay(pdMS_TO_TICKS(500));
  if (WiFi.status() != WL_CONNECTED) return false;
  configTime(0, 0, "pool.ntp.org", "time.google.com");   // UTC; used for reading timestamps
  return true;
}

// ------------------------------------------------------------------ uploads

// One reading in the original single-reading format (also what an older backend understands).
int postSingle(const Rec &r, uint32_t ts) {
  Out o(batchBuf, sizeof batchBuf);
  putSingleBody(o, netCfg.dev, r, ts, telemetryNow());
  return o.full ? -2 : httpPost("/api/readings", batchBuf);
}

// Up to `n` readings in one request. Readings with no usable timestamp are left out; the caller still removes
// them from the queue. Returns the HTTP status, or 200 if nothing was sendable.
int postBatch(const Rec *recs, const uint32_t *ts, int n) {
  Out o(batchBuf, sizeof batchBuf);
  int count = putBatchBody(o, netCfg.dev, telemetryNow(), recs, ts, n);
  if (count == 0) return 200;
  if (o.full) return -2;
  return httpPost("/api/readings/batch", batchBuf);
}

// Sends the oldest queued readings. Returns how long the task should wait before the next call.
uint32_t uploadOnce() {
  Rec recs[BATCH_MAX];
  uint32_t ts[BATCH_MAX];
  qLock(); int n = queue.peek(recs, BATCH_MAX); qUnlock();
  if (n == 0) return 1000;

  time_t nowT = time(nullptr);
  uint32_t nowEpoch = nowT > 1700000000 ? (uint32_t)nowT : 0, bootS = millis() / 1000;
  for (int i = 0; i < n; i++) ts[i] = repairedTs(recs[i], bootId, nowEpoch, bootS);

  int done = 0, code = 0;
  bool wantSingles = !serverHasBatch;
  if (!wantSingles) {
    code = postBatch(recs, ts, n);
    if (code == 200 || code == 201) done = n;
    else if (code == 404) { serverHasBatch = false; return 100; }          // old backend: use single posts from now on
    else if (code == 400 && n > 1) wantSingles = true;                      // find the bad reading one by one
  }
  if (wantSingles) {                                                        // the server stamps readings that have no ts
    for (int i = 0; i < n; i++) {
      code = postSingle(recs[i], ts[i]);
      if (code == 200 || code == 201 || code == 400 || code == 409) {
        if (code == 400 || code == 409) Serial.printf("Server rejected a reading (%d); dropping it.\n", code);
        done++;
      } else break;
    }
  }
  if (done) { qLock(); queue.pop(done); qUnlock(); }

  if (code == 200 || code == 201 || code == 400 || code == 409) { netState = NET_OK; return done == n ? 100 : 1000; }
  if (code == 401 || code == 403) {
    netState = NET_AUTH;
    Serial.printf("Server says %d: check this node's token and device id.\n", code);
    return 30000;
  }
  netState = NET_OFFLINE;
  return 5000;
}

// ------------------------------------------------------------ check-in and OTA

#if OTA_ENABLED
// Downloads the image the server offered, checks its SHA-256 BEFORE activating it, then restarts into it.
// The old firmware stays in place if anything goes wrong. There is no automatic rollback after a bad image
// that passes the checksum: only offer images you have tested.
bool performOta(const UpdateOffer &u) {
  bool tls = strncmp(netCfg.url, "https", 5) == 0;
  if (!(OTA_ALLOW_INSECURE || (tls && !TLS_INSECURE))) {
    Serial.println("OTA skipped: it needs TLS_INSECURE 0 (or OTA_ALLOW_INSECURE 1 for bench tests).");
    return false;
  }
  char url[240];
  snprintf(url, sizeof url, "%s%s?device_id=%s", netCfg.url, u.path, netCfg.dev);
  HTTPClient http;
  WiFiClientSecure secure;
  WiFiClient plain;
  beginHttp(http, secure, plain, url);
  int code = http.GET();
  if (code != 200 || (uint32_t)http.getSize() != u.size) {
    Serial.printf("OTA: download refused (HTTP %d, size %d)\n", code, http.getSize());
    http.end();
    return false;
  }
  if (!Update.begin(u.size, U_FLASH)) { http.end(); return false; }
  mbedtls_sha256_context sha;
  mbedtls_sha256_init(&sha);
  mbedtls_sha256_starts(&sha, 0);
  WiFiClient *stream = http.getStreamPtr();
  uint8_t buf[1024];
  uint32_t left = u.size;
  unsigned long lastData = millis();
  bool ok = true;
  while (left > 0 && ok) {
    size_t avail = stream->available();
    if (avail) {
      size_t want = avail < sizeof buf ? avail : sizeof buf;
      if (want > left) want = left;
      int got = stream->readBytes(buf, want);
      if (got <= 0 || Update.write(buf, got) != (size_t)got) ok = false;
      else { mbedtls_sha256_update(&sha, buf, got); left -= got; lastData = millis(); }
    } else if (millis() - lastData > 20000 || !http.connected()) {
      ok = false;
    } else {
      vTaskDelay(pdMS_TO_TICKS(2));
    }
  }
  uint8_t digest[32];
  mbedtls_sha256_finish(&sha, digest);
  mbedtls_sha256_free(&sha);
  http.end();
  char hex[65];
  for (int i = 0; i < 32; i++) snprintf(hex + i * 2, 3, "%02x", digest[i]);
  if (!ok || left != 0 || !hexEqualNoCase(hex, u.sha256)) {
    Update.abort();
    Serial.println("OTA: download incomplete or checksum mismatch; keeping the current firmware.");
    return false;
  }
  if (!Update.end(true)) { Serial.println("OTA: could not finalise the update."); return false; }
  Serial.printf("OTA: %s installed, restarting.\n", u.version);
  delay(500);
  ESP.restart();
  return true;
}
#endif

void doCheckin() {
  if (!serverHasCheckin) return;
  Out o(batchBuf, sizeof batchBuf);
  putCheckinBody(o, netCfg.dev, telemetryNow());
  int code = httpPost("/api/device/checkin", batchBuf, respBuf, sizeof respBuf);
  if (code == 404) { serverHasCheckin = false; return; }                    // older backend: nothing to do
  if (code == 401 || code == 403) { netState = NET_AUTH; return; }
  if (code != 200) return;

  if (applyConfig(settings, respBuf)) {
    saveSettings();
    Serial.printf("Settings updated by the server: read %lu s, send %lu s, pH %.1f-%.1f, turbidity %.1f NTU\n",
                  (unsigned long)(settings.read_ms / 1000), (unsigned long)(settings.send_ms / 1000),
                  settings.ph_min, settings.ph_max, settings.turb_max);
  }
  char st[24];                              // no NTP yet (a network that blocks it): take the server's clock
  if (time(nullptr) < 1700000000 && jsonStr(respBuf, "server_time", st, sizeof st)) {
    uint32_t t;
    if (epochFromIso(st, &t)) { struct timeval tv = {(time_t)t, 0}; settimeofday(&tv, nullptr); }
  }
#if OTA_ENABLED
  UpdateOffer u;
  if (parseUpdate(respBuf, u) && strcmp(u.version, FW_VERSION) != 0) performOta(u);
#endif
}

// ------------------------------------------------------------------ the task

void netTask(void *) {
  int failedConnects = 0;
  bool needCheckin = true;
  unsigned long lastCheckin = 0;
  for (;;) {
    if (portalRequested) {
      runPortal(PORTAL_SECONDS);
      portalRequested = false;
      failedConnects = 0;
      continue;
    }
    if (WiFi.status() != WL_CONNECTED) {
      netState = NET_OFFLINE;
      if (!connectWifi()) {
        if (++failedConnects >= PORTAL_AFTER_FAILS) portalRequested = true;   // wrong password or moved house: let someone fix it
        vTaskDelay(pdMS_TO_TICKS(5000));
        continue;
      }
      failedConnects = 0;
      needCheckin = true;
    }
    if (needCheckin || millis() - lastCheckin >= CHECKIN_PERIOD_MS) {
      needCheckin = false;
      lastCheckin = millis();
      doCheckin();
    }
    vTaskDelay(pdMS_TO_TICKS(uploadOnce()));
  }
}
