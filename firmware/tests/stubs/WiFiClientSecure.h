#pragma once
#include "WiFi.h"
struct WiFiClientSecure : WiFiClient { void setInsecure() {} void setCACert(const char *) {} };
