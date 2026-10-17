// Copy to secrets.h and fill in. secrets.h is in .gitignore.
// These are only the compile-time defaults: values saved through the WiFi setup portal (hold BOOT at power-up,
// or type "portal" in the Serial Monitor) take precedence, so a node can be re-pointed without re-flashing.
#pragma once

#define WIFI_SSID     "your-phone-hotspot"
#define WIFI_PASS     "your-password"
// No trailing slash. https://... for Render; http://<laptop-ip>:5000 for a local server
// (laptop and ESP32 must be on the same hotspot).
#define SERVER_URL    "https://your-app.onrender.com"
// The node's own token, printed once by:  python -m flask --app app register-device tank-test-01
// (the shared DEVICE_TOKEN from the server's .env also works until you give the node its own).
#define DEVICE_TOKEN  "sw_paste-the-token-printed-by-register-device"
#define DEVICE_ID     "tank-test-01"

// Only needed when TLS_INSECURE is 0: the PEM root certificate of your host.
// const char* ROOT_CA_PEM = "-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----\n";
