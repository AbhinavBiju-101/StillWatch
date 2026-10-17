# Firmware

| Sketch | Purpose |
|---|---|
| `sensor_serial_test/` | Step 1: serial-only sensor check (no WiFi). Use it first when the hardware is back. |
| `water_node/` | The real node (v0.5.0): sensors, LCD, LED, buzzer, local alert rules, crash-safe upload queue, check-in, optional OTA. |

## Files in `water_node/`

| File | What it holds |
|---|---|
| `water_node.ino` | setup/loop, sensor reading, LCD/LED/buzzer, serial commands |
| `config.h` | pins, which sensors are fitted, periods, limits, queue size, OTA switches |
| `logic.h` | hardware-free logic: CRC, trimmed mean, sensor-fault test, pH/NTU/TDS/ORP conversion, record format, JSON writer/parser |
| `queue.h` | `PersistentQueue`: append-log queue with crash-safe compaction |
| `fsstore.h` | LittleFS adapter for the queue |
| `net.h` | HTTP, batch upload, check-in task, OTA |
| `portal.h` | WiFi/server setup portal |
| `secrets.example.h` | copy to `secrets.h` (git-ignored) |

`logic.h` and `queue.h` use no Arduino headers, so they are tested on a PC (see Tests).

## Arduino IDE setup

1. Install **esp32 by Espressif**, version **3.x** (the buzzer uses `tone()`).
2. Library Manager: **LiquidCrystal I2C** (Frank de Brabander). If you set `HAVE_TEMP 1` also install **OneWire** and **DallasTemperature**.
3. Board: **ESP32S2 Dev Module**. Partition scheme: any with LittleFS (default is fine); for OTA choose one with two app slots (e.g. "Default 4MB with spiffs"). If the Serial Monitor is blank: Tools, USB CDC On Boot, Enabled.
4. Copy `secrets.example.h` to `secrets.h` and fill it in. Give the node its own token:
   `python -m flask --app app register-device tank-test-01` prints it once.
5. Check `config.h`: `LED_COMMON_ANODE`, `LCD_I2C_ADDR` (0x27 or 0x3F), and `HAVE_TDS` / `HAVE_TEMP` / `HAVE_ORP` for what is wired.
6. Upload; open Serial Monitor at 115200.

## Wiring added in v0.5

| Sensor | Pin | Notes |
|---|---|---|
| DS18B20 temperature | GPIO 11 | 4.7 k pull-up from data to 3.3 V. Also used to temperature-compensate pH. |
| Analog ORP module | GPIO 10 | output 0-3 V, no divider. Check `ORP_ZERO_MV` / `ORP_GAIN` against your module's datasheet. |
| TDS probe | GPIO 2 | 5 V output needs the two-resistor divider. |
| BOOT button | GPIO 0 | hold 3 s at power-up for the setup portal |

pH, TDS and turbidity outputs are 0-5 V: the equal-resistor divider must be in place before they touch the ESP32.

## Behaviour

- Reads every 10 s: 64 samples, 6 lowest and highest discarded, then averaged. A pin stuck near 0 V or 5 V, or samples
  scattered over more than `FAULT_SPREAD_MV`, marks that sensor as **faulty**: its value is left out of the upload, and the
  fault list is sent with the next check-in so the website shows "sensor problem reported by the device".
- One reading per minute goes to a flash queue (LittleFS, up to 4000 readings, about 66 h). Records carry a CRC; a power cut
  mid-write loses at most the record being written. Uploads go oldest-first in batches of 20 to `/api/readings/batch`. The
  server answers 200/201 when a request is finished (bad rows are reported but never block the rest), and the node then drops
  the whole batch. Network or server errors keep the data and retry.
- If the clock was not set yet when a reading was taken, its time is repaired from the first trustworthy time afterwards.
- Check-in every 5 minutes: sends RSSI, uptime, queue length, boot reason and faults; receives the server time, the read/send
  periods and the limits, and any firmware offer. Limits can therefore be changed at the server without re-flashing.
- Local rule (works with no WiFi): red LED + beep + "TEST RECOMMENDED" if pH is outside the limits or turbidity is above the limit.
- pH is temperature-compensated (Nernst slope, pivot at pH 7) when a temperature reading is valid; the same formula is in `app/calibration.py`.
  Raw millivolts are always uploaded.

## Setup portal

Hold BOOT for 3 s at power-up, type `portal`, or let it open by itself after 24 failed WiFi attempts. The node starts a WiFi
network with a random password (printed on the Serial Monitor and shown on the LCD); open the address it shows and enter WiFi
name/password, server URL, device id and token. Values are saved to flash and take precedence over `secrets.h`.

## Over-the-air updates (off by default)

Set `OTA_ENABLED 1` and `TLS_INSECURE 0` with `ROOT_CA_PEM` in `secrets.h`. Then on the server:

```
python -m flask --app app fw-release path/to/water_node.ino.bin 0.5.1
python -m flask --app app fw-assign 0.5.1 --device tank-test-01
```

At the next check-in the node downloads only the image assigned to it, verifies its SHA-256 before activating it, and restarts.
`OTA_ALLOW_INSECURE 1` exists for bench tests only. **Not provided:** automatic rollback if the new image fails to run, and image
signing (the hash comes from the same server as the image). Test an update on a node you can reach before using it on others.

## Serial commands

`cal4`, `cal7` (pH buffers; saved to flash), `calorp <mV>` (ORP offset against a reference solution), `clearcal`, `ledtest`,
`status`, `info`, `portal`, `clearq` (empty the upload queue), `reboot`.

## Tests (on a PC, no hardware)

```
sh firmware/tests/run.sh            # logic + queue, g++ with AddressSanitizer and UBSan
sh firmware/tests/compile_check.sh  # type-checks the sketch against small Arduino stubs
```

The Python test `tests/test_firmware_contract.py` makes the C++ code write its real request bodies and checks that the Flask
validator accepts them, and that the node's parser understands the server's check-in reply.
**The stubs are written from memory of the esp32 3.x API, so the sketch has not been compiled by the real core.** Compile it once
in the Arduino IDE and fix any API differences before relying on it.

## Finding out your RGB LED type

Wire the LED's longest leg (the common pin) to **GND**, and each colour leg through a 220 ohm resistor to GPIO 4 (red), 5 (green),
6 (blue, unused). Send `ledtest`: you should see red, then green.

- Red then green: common cathode, `LED_COMMON_ANODE 0` is correct.
- Nothing lights: probably common anode. Move the long leg to **3.3V**, set `LED_COMMON_ANODE 1`, re-upload, run `ledtest` again.
- Never connect the common leg to 5V on a 3.3V GPIO circuit.

## Before trusting the numbers

- The turbidity NTU curve is a generic datasheet polynomial: calibrate against known standards.
- Fault thresholds, ORP conversion, flash wear and the portal have not been tried on real hardware.
