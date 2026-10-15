# Firmware

| Sketch | Purpose |
|---|---|
| `sensor_serial_test/` | Step 1: serial-only sensor check (no WiFi). Use it first when the hardware is back. |
| `water_node/` | The real node: sensors, LCD, LED, buzzer, local alert rules, upload queue. |

## Arduino IDE setup (water_node)

1. Install the **esp32 by Espressif** board package, version **3.x** (the buzzer uses `tone()`).
2. Library Manager: install **LiquidCrystal I2C** (Frank de Brabander).
3. Board: **ESP32S2 Dev Module**. If the Serial Monitor is blank: Tools, USB CDC On Boot, Enabled.
4. Copy `secrets.example.h` to `secrets.h` and fill it in (hotspot name/password, server URL, device token, device id).
   `secrets.h` is git-ignored.
5. Check `config.h`: `LED_COMMON_ANODE`, `LCD_I2C_ADDR` (0x27 or 0x3F), `HAVE_TDS`.
6. Upload; open Serial Monitor at 115200.

## Behaviour

- Reads pH and turbidity every 10 s (64-sample average), updates the LCD and LED, and queues one reading per
  minute for upload.
- Local rule: red LED + beep + "TEST RECOMMENDED" if pH is outside 6.5-8.5 or turbidity is above 5 NTU.
  Works with no WiFi. Keep these limits in sync with `app/detectors.py`.
- A background task uploads the queue oldest-first. Failed uploads are retried with the same timestamp, which
  the server treats as a duplicate, so retries never double-count. The queue holds 120 readings in RAM and is
  lost on reboot.
- pH reads `--` and is left out of uploads until calibrated. Raw millivolts are always uploaded.

## Finding out your RGB LED type

Wire the LED's longest leg (the common pin) to **GND**, and each colour leg through a 220 ohm resistor to
GPIO 4 (red), 5 (green), 6 (blue, unused). Send `ledtest` in the Serial Monitor: you should see red, then green.

- Red then green: it is common cathode. `LED_COMMON_ANODE 0` is correct.
- Nothing lights: it is probably common anode. Move the long leg to **3.3V**, set `LED_COMMON_ANODE 1`,
  re-upload, and run `ledtest` again.
- Never connect the common leg to 5V on a 3.3V GPIO circuit.

## Calibrating pH (Serial Monitor)

Probe in pH 4 buffer, wait about a minute, send `cal4`. Rinse, pH 7 buffer, wait, send `cal7`.
Values are saved to flash. `clearcal` wipes them; `status` prints WiFi/queue state.

## Before trusting the numbers

- The turbidity NTU curve is a generic datasheet polynomial: calibrate it against known standards.
- Voltage dividers (two equal resistors per 0-5 V output) must be in place before wiring sensors to the ESP32.
