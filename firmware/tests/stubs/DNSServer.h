#pragma once
#include "WiFi.h"
struct DNSServer { bool start(int, const String &, const IPAddress &) { return true; } void processNextRequest() {} void stop() {} };
