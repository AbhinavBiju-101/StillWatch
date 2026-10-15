// Copy to secrets.h and fill in. secrets.h is in .gitignore.
#pragma once

#define WIFI_SSID     "your-phone-hotspot"
#define WIFI_PASS     "your-password"
// No trailing slash. https://... for Render; http://<laptop-ip>:5000 for a local server
// (laptop and ESP32 must be on the same hotspot).
#define SERVER_URL    "https://your-app.onrender.com"
#define DEVICE_TOKEN  "same-value-as-DEVICE_TOKEN-on-the-server"
#define DEVICE_ID     "tank-test-01"

// Only needed when TLS_INSECURE is 0: the PEM root certificate of your host.
// const char* ROOT_CA_PEM = "-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----\n";
