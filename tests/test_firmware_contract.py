"""The firmware and the backend share nothing but a JSON contract. These tests compile the firmware's pure logic
with g++ and check both directions: what the node sends is accepted by schema.py, and what the server replies
is understood by the node. Skipped when no C++ compiler is installed."""
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timezone

from app import calibration, create_app, devices, schema

ROOT = os.path.join(os.path.dirname(__file__), "..", "firmware")
CXX = shutil.which("g++") or shutil.which("c++")


@unittest.skipUnless(CXX, "needs a C++ compiler")
class FirmwareContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exe = os.path.join(tempfile.mkdtemp(), "fw_test")
        subprocess.run([CXX, "-std=c++17", "-Wall", "-Wextra", "-Wno-unused-parameter",
                        "-I", os.path.join(ROOT, "water_node"), os.path.join(ROOT, "tests", "test_logic.cpp"),
                        "-o", cls.exe], check=True)

    def run_exe(self, *args, stdin=None):
        return subprocess.run([self.exe, *args], input=stdin, capture_output=True, text=True, check=True).stdout

    def test_firmware_unit_tests_pass(self):
        self.assertIn("all checks passed", self.run_exe())

    def bodies(self):
        base = int(time.time()) - 600
        out = {}
        for line in self.run_exe("dump", str(base)).splitlines():
            tag, _, body = line.partition(":")
            out[tag] = json.loads(body)              # must be valid JSON, with no NaN or trailing commas
        return base, out

    def test_single_reading_is_accepted(self):
        base, out = self.bodies()
        clean, errors = schema.validate(out["SINGLE"])
        self.assertEqual(errors, [])
        self.assertEqual(clean["ts"], datetime.fromtimestamp(base, timezone.utc))
        self.assertEqual((clean["ph_raw_mv"], clean["tds_raw_mv"], clean["orp_raw_mv"]), (2480, 800, 1500))
        self.assertEqual(clean["telemetry"], {"fw": "0.5.0", "rssi": -63, "uptime_s": 4000, "queued": 3,
                                              "boot": "power_on", "faults": ["orp"]})

    def test_batch_is_accepted_and_drops_unplaceable_readings(self):
        base, out = self.bodies()
        batch = out["BATCH2"]                        # 3 queued, the one with no timestamp is left out
        device_id, cleans, rejected, telemetry, errors = schema.validate_batch(batch)
        self.assertEqual((errors, rejected, device_id), ([], [], "tank-fw-01"))
        self.assertEqual(len(cleans), 2)
        self.assertNotIn("ph", cleans[1])            # uncalibrated pH is omitted, not sent as NaN
        self.assertEqual(telemetry["faults"], ["orp"])

    def test_checkin_body_is_accepted(self):
        _, out = self.bodies()
        telemetry, errors = schema.validate_telemetry(out["CHECKIN"])
        self.assertEqual(errors, [])
        self.assertEqual(out["CHECKIN"]["schema"], 1)

    def test_node_understands_the_servers_checkin_reply(self):
        app = create_app({"TESTING": True, "SECRET_KEY": "t", "DEVICE_READ_PERIOD_S": 20, "DEVICE_SEND_PERIOD_S": 90})
        with app.app_context():
            from flask import jsonify
            sha = "ab" * 32
            reply = jsonify(ok=True, server_time="2026-10-17T08:00:00Z",
                            config=devices.settings_for(app.config, {"settings": {"send_period_s": 120}}),
                            update={"version": "0.6.0", "sha256": sha, "size": 1048576,
                                    "path": "/api/firmware/0.6.0.bin"}).get_data(as_text=True)
        line = self.run_exe("parse", stdin=reply).strip()
        got = dict(kv.split("=", 1) for kv in line.split())
        self.assertEqual((got["read_ms"], got["send_ms"]), ("20000", "120000"))     # per-device override wins
        self.assertEqual((got["ph_min"], got["ph_max"], got["turb"]), ("6.50", "8.50", "5.00"))
        self.assertEqual((got["update"], got["sha"], got["size"], got["path"]),
                         ("0.6.0", sha, "1048576", "/api/firmware/0.6.0.bin"))

    def test_ph_formula_matches_the_servers(self):
        params = calibration.ph_two_point(3000, 2500)
        params["cal_temp_c"] = 25
        for mv, temp, expect in ((2750, 25, 5.5), (2750, 45, 5.594295), (2500, 45, 7.0)):
            self.assertAlmostEqual(calibration.ph_from_mv(mv, params, temp), expect, places=5)
        self.assertAlmostEqual(calibration.ph_from_mv(2750, params, None), 5.5, places=5)   # same numbers as test_logic.cpp


if __name__ == "__main__":
    unittest.main()
