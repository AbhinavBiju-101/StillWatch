#pragma once
#include "WiFi.h"
#define HTTP_GET 1
#define HTTP_POST 3
struct WebServer {
  WebServer(int) {}
  void on(const char *, int, std::function<void()>) {}
  void onNotFound(std::function<void()>) {}
  void begin() {} void stop() {} void handleClient() {}
  String arg(const char *) { return String(); }
  void send(int, const char *, const String &) {}
  void send(int, const char *, const char *) {}
  void sendHeader(const char *, const String &) {}
};
