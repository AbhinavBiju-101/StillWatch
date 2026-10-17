// WiFi setup portal: the node becomes an access point with a one-page form, so the WiFi name, password, server
// address and device token can be changed without re-flashing. Opens when BOOT is held at power-up, when you type
// "portal" in the Serial Monitor, when no WiFi is configured, or after PORTAL_AFTER_FAILS failed connection
// attempts. The access point is protected by a random password shown on the LCD and in the Serial Monitor.
// Included by water_node.ino after net.h.
#pragma once

static String htmlEscape(const char *s) {
  String o;
  for (; *s; s++) {
    switch (*s) {
      case '&': o += "&amp;"; break;
      case '<': o += "&lt;"; break;
      case '>': o += "&gt;"; break;
      case '"': o += "&quot;"; break;
      default: o += *s;
    }
  }
  return o;
}

static void makePortalPassword(char *out) {                  // 8 characters, no look-alikes (0/o, 1/l/i)
  static const char alphabet[] = "abcdefghjkmnpqrstuvwxyz23456789";
  for (int i = 0; i < 8; i++) out[i] = alphabet[esp_random() % (sizeof alphabet - 1)];
  out[8] = 0;
}

void runPortal(int seconds) {
  char apName[24], apPass[9];
  uint8_t mac[6];
  WiFi.macAddress(mac);
  snprintf(apName, sizeof apName, "StillWatch-%02X%02X", mac[4], mac[5]);
  makePortalPassword(apPass);

  WiFi.disconnect();
  WiFi.mode(WIFI_AP_STA);
  WiFi.softAP(apName, apPass);
  IPAddress ip = WiFi.softAPIP();
  DNSServer dns;
  dns.start(53, "*", ip);                    // captive portal: any name opens the form
  WebServer web(80);

  web.on("/", HTTP_GET, [&]() {
    String page = F("<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
                    "<title>StillWatch setup</title><style>body{font:16px system-ui;margin:1.2rem;max-width:30rem}"
                    "label{display:block;margin:.8rem 0 .2rem;font-weight:600}input{width:100%;padding:.5rem;font:inherit}"
                    "button{margin-top:1.2rem;padding:.6rem 1.2rem;font:inherit}small{color:#555}</style>"
                    "<h1>StillWatch node setup</h1><form method=post action=/save>");
    page += "<label>WiFi name</label><input name=ssid maxlength=32 value=\"" + htmlEscape(netCfg.ssid) + "\">";
    page += "<label>WiFi password</label><input name=pass type=password maxlength=64 placeholder=\"(unchanged)\">";
    page += "<label>Server address</label><input name=url maxlength=95 value=\"" + htmlEscape(netCfg.url) + "\">";
    page += "<small>For example https://your-app.onrender.com, no trailing slash</small>";
    page += "<label>Device id</label><input name=dev maxlength=64 value=\"" + htmlEscape(netCfg.dev) + "\">";
    page += "<label>Device token</label><input name=token type=password maxlength=71 placeholder=\"(unchanged)\">";
    page += "<small>Printed once by <code>flask register-device</code></small>";
    page += "<br><button>Save and restart</button></form>";
    web.send(200, "text/html", page);
  });

  web.on("/save", HTTP_POST, [&]() {
    String ssid = web.arg("ssid"), pass = web.arg("pass"), url = web.arg("url"), dev = web.arg("dev"), tok = web.arg("token");
    ssid.trim(); url.trim(); dev.trim(); tok.trim();
    while (url.endsWith("/")) url.remove(url.length() - 1);
    bool ok = ssid.length() >= 1 && ssid.length() <= 32 && pass.length() <= 64 && dev.length() >= 1 && dev.length() <= 64 &&
              (url.startsWith("http://") || url.startsWith("https://")) && url.length() < 96 && tok.length() <= 71;
    for (size_t i = 0; ok && i < dev.length(); i++) {
      char c = dev[i];
      if (!(isalnum((unsigned char)c) || c == '-' || c == '_')) ok = false;     // same rule as the server's device_id
    }
    if (!ok) { web.send(400, "text/plain", "Some values are missing or not allowed. Go back and check them."); return; }
    prefs.begin("net", false);
    prefs.putString("ssid", ssid);
    if (pass.length()) prefs.putString("pass", pass);          // a blank password field keeps the saved one
    prefs.putString("url", url);
    prefs.putString("dev", dev);
    if (tok.length()) prefs.putString("token", tok);
    prefs.end();
    web.send(200, "text/html", "<meta name=viewport content='width=device-width,initial-scale=1'><p>Saved. The node is restarting.");
    delay(1500);
    ESP.restart();
  });
  web.onNotFound([&]() {                                        // captive-portal probes land here: send them to the form
    web.sendHeader("Location", String("http://") + ip.toString() + "/");
    web.send(302, "text/plain", "");
  });
  web.begin();

  snprintf(portalL0, sizeof portalL0, "%s", apName);
  snprintf(portalL1, sizeof portalL1, "pw %s", apPass);
  portalActive = true;
  Serial.printf("WiFi setup portal: join \"%s\", password %s, then open http://%s/\n", apName, apPass, ip.toString().c_str());

  unsigned long end = millis() + (unsigned long)seconds * 1000UL;
  while (millis() < end) {
    dns.processNextRequest();
    web.handleClient();
    vTaskDelay(pdMS_TO_TICKS(5));
  }
  web.stop();
  dns.stop();
  portalActive = false;
  WiFi.softAPdisconnect(true);
  WiFi.mode(WIFI_STA);
  Serial.println("WiFi setup portal closed.");
}
