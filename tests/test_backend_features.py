"""Pure and stubbed tests for the device registry, batch/telemetry validation, lab parsing, training rows,
device-offline wording and the API's auth rules. Real-database behaviour is in test_integration_pg.py."""
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest import mock

from app import create_app, devices, labs, notify, schema, service, training

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
CFG = {"TESTING": True, "SECRET_KEY": "t", "DEVICE_TOKEN": "shared", "DEMO_PASSWORD": "d",
       "TESTING_PASSWORD": "t", "DATABASE_URL": "x", "SWEEP_TOKEN": "sw"}


@contextmanager
def fake_connection():
    yield object()


class TelemetryAndBatch(unittest.TestCase):
    def reading(self, minutes_ago, **kw):
        return {"ts": (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ"), "ph": 7.1, **kw}

    def test_telemetry_is_validated(self):
        ok, errors = schema.validate_telemetry({"rssi": -60, "uptime_s": 10, "queued": 0, "boot": "power_on",
                                                "faults": ["ph", "ph", "orp"]})
        self.assertEqual((errors, ok["faults"]), ([], ["orp", "ph"]))
        for bad in ({"rssi": 5}, {"rssi": "x"}, {"boot": "a;drop table"}, {"faults": ["water"]}, {"faults": "ph"},
                    {"queued": -1}, {"uptime_s": True}):
            self.assertTrue(schema.validate_telemetry(bad)[1], bad)

    def test_timestamps_from_a_bad_clock_are_refused(self):
        base = {"schema": 1, "device_id": "t", "ph": 7}
        for ts in ("1970-01-01T00:00:10Z", "2026-12-01T00:00:00Z"):
            clean, errors = schema.validate({**base, "ts": ts}, now=NOW)
            self.assertIsNone(clean)
            self.assertTrue(any("clock" in e for e in errors), (ts, errors))
        self.assertEqual(schema.validate({**base, "ts": "2026-10-17T11:55:00Z"}, now=NOW)[1], [])

    def test_batch_reports_bad_rows_without_blocking_the_rest(self):
        body = {"schema": 1, "device_id": "t", "rssi": -50,
                "readings": [self.reading(30), {"ph": 7}, self.reading(20, ph=99), self.reading(10)]}
        device_id, cleans, rejected, telemetry, errors = schema.validate_batch(body, now=NOW)
        self.assertEqual((errors, len(cleans), [r["index"] for r in rejected]), ([], 2, [1, 2]))
        self.assertEqual(telemetry["rssi"], -50)

    def test_batch_envelope_errors(self):
        for body in ({"schema": 1, "device_id": "t", "readings": []},
                     {"schema": 1, "device_id": "t", "readings": [self.reading(1)] * (schema.BATCH_MAX + 1)},
                     {"schema": 2, "device_id": "t", "readings": [self.reading(1)]},
                     {"schema": 1, "device_id": "bad id", "readings": [self.reading(1)]}, []):
            self.assertTrue(schema.validate_batch(body, now=NOW)[4], body)


class DeviceRegistry(unittest.TestCase):
    def test_tokens_are_hashed_and_prefixed(self):
        t = devices.new_token()
        self.assertTrue(t.startswith("sw_") and len(t) > 30)
        self.assertEqual(len(devices.hash_token(t)), 64)
        self.assertNotIn(t, devices.hash_token(t))

    def test_identity_rules(self):
        cfg = {"DEVICE_TOKEN": "shared", "ALLOW_SHARED_DEVICE_TOKEN": True}
        self.assertEqual(devices.identify(cfg, "shared").kind, "shared")
        self.assertIsNone(devices.identify(cfg, ""))
        self.assertIsNone(devices.identify(cfg, "nope"))                       # no database touched for a non-token
        self.assertIsNone(devices.identify({**cfg, "ALLOW_SHARED_DEVICE_TOKEN": False}, "shared"))
        row = {"device_id": "a", "settings": {}}
        with mock.patch("app.db.connection", fake_connection), mock.patch("app.db.one", return_value=row):
            who = devices.identify(cfg, "sw_whatever")
        self.assertEqual(who.kind, "device")
        self.assertTrue(who.allows("a") and not who.allows("b"))

    def test_settings_for_applies_only_sane_overrides(self):
        cfg = {"DEVICE_READ_PERIOD_S": 10, "DEVICE_SEND_PERIOD_S": 60}
        self.assertEqual(devices.settings_for(cfg, None)["ph_min"], 6.5)
        s = devices.settings_for(cfg, {"settings": {"read_period_s": 30, "send_period_s": 1}})
        self.assertEqual((s["read_period_s"], s["send_period_s"]), (30, 60))     # 1 s is out of range: ignored

    def test_firmware_release_checks(self):
        conn = object()
        with mock.patch("app.db.run"):
            for image, version in ((b"\xe9" + b"x" * 100, "1.0"), (b"\x00" * 70000, "1.0"),
                                   (b"\xe9" + b"x" * 70000, "bad version!")):
                with self.assertRaises(ValueError):
                    devices.add_release(conn, version, image)
            self.assertEqual(len(devices.add_release(conn, "1.0.0", b"\xe9" + b"x" * 70000)), 64)


class LabParsing(unittest.TestCase):
    def test_readings_with_qualifiers(self):
        self.assertEqual(labs.parse_reading("7.4"), ("=", 7.4))
        self.assertEqual(labs.parse_reading("<1"), ("<", 1.0))
        self.assertEqual(labs.parse_reading(">=10"), (">", 10.0))
        self.assertEqual(labs.parse_reading(" Absent "), ("absent", None))
        self.assertEqual(labs.parse_reading("ND"), ("absent", None))
        for bad in ("", "seven", "1e5", "<"):
            with self.assertRaises(ValueError):
                labs.parse_reading(bad)

    def test_legacy_columns_follow_the_parameters(self):
        v = [labs.parse_value({"parameter": "ph", "text": "7.4"}), labs.parse_value({"parameter": "turbidity", "text": "<1"}),
             labs.parse_value({"parameter": "tds", "text": "310"}), labs.parse_value({"parameter": "e_coli", "text": "absent"})]
        self.assertEqual(labs.legacy_columns(v), (7.4, None, 310.0, "absent"))
        v.append(labs.parse_value({"parameter": "total_coliform", "text": "2"}))
        self.assertEqual(labs.legacy_columns(v)[3], "present")
        self.assertEqual(labs.legacy_columns([v[0]])[3], "not_tested")

    def test_sample_validation(self):
        body = {"device_id": "t", "collected_at": "2026-10-01T08:00:00Z", "values": [{"parameter": "ph", "text": "7"}]}
        self.assertEqual(labs.parse_sample(body)["values"][0]["unit"], None)
        for bad in ({**body, "values": []}, {**body, "collected_at": "soon"}, {**body, "collected_at": "2099-01-01T00:00:00Z"},
                    {**body, "values": [{"parameter": "ph", "text": "7"}] * 2},
                    {**body, "values": [{"parameter": "Bad Name!", "text": "7"}]},
                    {**body, "values": [{"parameter": "x", "qualifier": "absent", "value": 3}]}, {"values": []}):
            with self.assertRaises(ValueError, msg=bad):
                labs.parse_sample(bad)

    def test_uploads_are_judged_by_content(self):
        self.assertEqual(labs.check_upload("My report (final).pdf", b"%PDF-1.7 ...")[0], "application/pdf")
        self.assertEqual(labs.check_upload("../../etc/passwd.png", b"\x89PNG\r\n\x1a\n...")[1], "etc_passwd.png")
        for name, data in (("a.pdf", b"MZ\x90 exe"), ("a.pdf", b""), ("a.pdf", b"%PDF-" + b"x" * labs.MAX_FILE)):
            with self.assertRaises(ValueError):
                labs.check_upload(name, data)


class ServiceBits(unittest.TestCase):
    def test_tank_status_shared_definition(self):
        recent, old = NOW - timedelta(minutes=5), NOW - timedelta(hours=2)
        self.assertEqual(service.tank_status("pilot", recent, None, NOW), "green")
        self.assertEqual(service.tank_status("pilot", old, None, NOW), "offline")
        self.assertEqual(service.tank_status("demo", old, None, NOW), "green")        # simulated data is never "offline"
        self.assertEqual(service.tank_status("pilot", None, None, NOW), "waiting")
        self.assertEqual(service.tank_status("pilot", old, "red", NOW), "red")

    def test_device_messages_never_talk_about_the_water(self):
        text = service.device_silent_message("Hostel A", 190)
        self.assertIn("3 h 10 min", text)
        self.assertIn("not the water quality", text)
        sms = notify.build_device_message("Hostel A", [{"score": 190}], "https://x.example", "t1")
        self.assertIn("no data from Hostel A", sms)
        self.assertNotRegex(sms.lower(), r"unusual change|safe")


class TrainingRows(unittest.TestCase):
    def make(self, n=300, spike_last=0):
        t0 = NOW - timedelta(minutes=5 * n)
        rows = []
        for i in range(n):
            late = i >= n - spike_last
            rows.append({"ts": t0 + timedelta(minutes=5 * i), "ph": 7.2, "turbidity_ntu": 8.0 if late else 1.0,
                         "tds_ppm": 300.0, "temp_c": None, "orp_mv": None})
        return rows

    def sample(self):
        return {"device_id": "t", "collected_at": NOW, "lab_name": "L", "sample_location": "outlet",
                "values": [{"parameter": "e_coli", "qualifier": "present", "value": None, "unit": "/100 mL"},
                           {"parameter": "ph", "qualifier": "=", "value": 7.3, "unit": ""}]}

    def test_features_use_only_the_past(self):
        calm = training.sample_row(self.sample(), self.make(), [], [])
        spiky = training.sample_row(self.sample(), self.make(spike_last=50), [], [])
        self.assertLess(calm["turbidity_ntu_max"], 2)
        self.assertEqual(spiky["turbidity_ntu_max"], 8.0)
        self.assertGreater(spiky["turbidity_ntu_z_vs_prior"], 5)
        self.assertTrue(spiky["turbidity_ntu_excursion"] and not calm["turbidity_ntu_excursion"])
        after = self.make() + [{"ts": NOW + timedelta(minutes=10), "ph": 7, "turbidity_ntu": 999.0, "tds_ppm": 1,
                                "temp_c": None, "orp_mv": None}]
        self.assertEqual(training.sample_row(self.sample(), after, [], [])["turbidity_ntu_max"], 1.0)   # no leakage

    def test_events_alerts_and_labels(self):
        ev = [{"ts": NOW - timedelta(hours=3), "kind": "cleaning"}, {"ts": NOW + timedelta(hours=1), "kind": "refill"}]
        al = [{"created_at": NOW - timedelta(hours=2), "severity": "red", "feedback": "confirmed"},
              {"created_at": NOW - timedelta(days=3), "severity": "yellow", "feedback": None}]
        r = training.sample_row(self.sample(), self.make(), ev, al)
        self.assertEqual((r["last_event_kind"], r["minutes_since_last_event"]), ("cleaning", 180))
        self.assertEqual((r["alerts_24h"], r["red_alerts_24h"], r["alerts_24h_confirmed"]), (1, 1, 1))
        self.assertEqual((r["lab_e_coli"], r["lab_ph"], r["lab_ph_q"]), ("present", 7.3, "="))

    def test_csv_is_formula_safe_and_unions_lab_columns(self):
        a = training.sample_row(self.sample(), self.make(), [], [])
        b = dict(a, lab_name="=HYPERLINK(1)", lab_fluoride=0.4)
        text = training.to_csv([a, b])
        header = text.splitlines()[0].split(",")
        self.assertIn("lab_fluoride", header)
        self.assertIn("'=HYPERLINK(1)", text)


class ApiAuth(unittest.TestCase):
    def setUp(self):
        self.client = create_app(CFG).test_client()

    def test_device_endpoints_need_a_token(self):
        for path in ("/api/readings", "/api/readings/batch", "/api/device/checkin"):
            self.assertEqual(self.client.post(path, json={}).status_code, 401, path)
            self.assertEqual(self.client.post(path, json={}, headers={"X-Device-Token": "x"}).status_code, 401, path)
        self.assertEqual(self.client.get("/api/firmware/1.0.bin?device_id=a").status_code, 401)

    def test_operator_endpoints_need_a_session_or_bearer_token(self):
        for path in ("/api/tanks", "/api/tanks/x", "/api/alerts"):
            self.assertEqual(self.client.get(path).status_code, 401, path)
        for path in ("/api/events", "/api/lab-results"):
            self.assertEqual(self.client.post(path, json={}).status_code, 401, path)
        self.assertEqual(self.client.get("/api/tanks", headers={"Authorization": "Bearer other"}).status_code, 401)

    def test_sweep_token(self):
        self.assertEqual(self.client.post("/api/sweep").status_code, 401)
        self.assertEqual(self.client.post("/api/sweep", headers={"X-Sweep-Token": "wrong"}).status_code, 401)
        off = create_app({**CFG, "SWEEP_TOKEN": ""}).test_client()
        self.assertEqual(off.post("/api/sweep", headers={"X-Sweep-Token": ""}).status_code, 401)   # empty = disabled

    def test_batch_envelope_error_is_400_and_binding_is_enforced(self):
        hdr = {"X-Device-Token": "shared"}
        r = self.client.post("/api/readings/batch", json={"schema": 1, "device_id": "t", "readings": []}, headers=hdr)
        self.assertEqual(r.status_code, 400)
        who = devices.Identity("device", {"device_id": "node-a"})
        good = {"schema": 1, "device_id": "node-b", "readings": [{"ts": (datetime.now(timezone.utc) - timedelta(minutes=1))
                                                                   .strftime("%Y-%m-%dT%H:%M:%SZ"), "ph": 7}]}
        with mock.patch("app.devices.identify", return_value=who), mock.patch("app.db.connection", fake_connection):
            r = self.client.post("/api/readings/batch", json=good, headers={"X-Device-Token": "sw_x"})
        self.assertEqual(r.status_code, 403)


if __name__ == "__main__":
    unittest.main()
