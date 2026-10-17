#!/bin/sh
# Syntax- and type-check the whole sketch on a normal computer, against stand-in Arduino headers (tests/stubs).
# This catches typos, missing declarations and wrong signatures. It cannot prove behaviour on the board, and the
# stubs are written from memory of the arduino-esp32 3.x API, so a real Arduino IDE compile is still the final check.
# Builds the sketch four ways: as shipped, and with every optional sensor / OTA switched on.
set -e
here="$(cd "$(dirname "$0")" && pwd)"
src="$here/../water_node"
for variant in default all_on; do
  tmp="$(mktemp -d)"
  cp "$src"/*.h "$tmp"/
  cp "$src/water_node.ino" "$tmp/water_node.cpp"
  [ -f "$tmp/secrets.h" ] || cp "$src/secrets.example.h" "$tmp/secrets.h"
  if [ "$variant" = all_on ]; then
    sed -i -e 's/#define HAVE_TDS .*/#define HAVE_TDS 1/' -e 's/#define HAVE_TEMP .*/#define HAVE_TEMP 1/' \
           -e 's/#define HAVE_ORP .*/#define HAVE_ORP 1/' -e 's/#define OTA_ENABLED .*/#define OTA_ENABLED 1/' \
           -e 's/#define OTA_ALLOW_INSECURE .*/#define OTA_ALLOW_INSECURE 1/' "$tmp/config.h"
  fi
  g++ -std=gnu++17 -fsyntax-only -Wall -Wextra -Wno-unused-parameter -Wno-unused-variable \
      -I"$here/stubs" -I"$tmp" -include Arduino.h "$tmp/water_node.cpp"
  echo "compile check ($variant): ok"
  rm -rf "$tmp"
done
