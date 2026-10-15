import unittest
from datetime import datetime, timezone

from app import detectors, schema, simulator, calibration


def to_series(msgs, upto=None):
    """Messages -> detector input, like service.load_series does (5 min cadence already)."""
    series = {m: [] for m in detectors.METRICS}
    for msg in msgs[:upto]:
        t = datetime.fromisoformat(msg["ts"].replace("Z", "+00:00")).timestamp()
        for m in detectors.METRICS:
            if msg.get(m) is not None:
                series[m].append((t, msg[m]))
    return series


class DetectorTests(unittest.TestCase):
    def test_normal_tanks_raise_no_findings(self):
        for seed in range(1, 21):
            msgs = simulator.generate_tank(seed)
            for i in range(40, len(msgs) + 1):       # evaluate at every step after warm-up
                f = detectors.analyze(to_series(msgs, upto=i))
                self.assertEqual(f, [], f"seed {seed} step {i}: {[x.detail for x in f]}")

    def test_event_is_detected_quickly(self):
        for seed in range(1, 11):
            msgs = simulator.generate_tank(seed, event_minutes_ago=100)
            # event starts at index len-1-20; check 30 min (6 steps) after onset
            onset = len(msgs) - 1 - 20
            f = detectors.analyze(to_series(msgs, upto=onset + 7))
            self.assertTrue(f, f"seed {seed}: nothing detected 30 min after onset")
            self.assertIn("turbidity_ntu", {x.metric for x in f})

    def test_no_detection_before_baseline(self):
        msgs = simulator.generate_tank(1, event_minutes_ago=100)
        f = detectors.analyze(to_series(msgs, upto=10))
        self.assertEqual([x for x in f if x.detector != "limit"], [])

    def test_limit_and_severity(self):
        series = {"ph": [(0, 9.1)]}
        f = detectors.analyze(series)
        self.assertEqual(detectors.severity(f), "red")
        self.assertIsNone(detectors.severity([]))

    def test_message_never_says_safe(self):
        f = [detectors.Finding("ph", "limit", 1.0, "pH 9.10 is above 8.5")]
        msg = detectors.template_message(f).lower()
        self.assertNotIn("safe", msg)
        self.assertIn("lab", msg)


class SchemaTests(unittest.TestCase):
    base = {"schema": 1, "device_id": "tank-01", "ts": "2026-10-09T10:15:00Z", "ph": 7.21}

    def test_valid_minimal(self):
        clean, errors = schema.validate(dict(self.base))
        self.assertEqual(errors, [])
        self.assertEqual(clean["ph"], 7.21)
        self.assertEqual(clean["ts"], datetime(2026, 10, 9, 10, 15, tzinfo=timezone.utc))
        self.assertNotIn("tds_ppm", clean)           # omitted sensors stay omitted

    def test_rejections(self):
        bad = [
            {**self.base, "schema": 2},
            {**self.base, "device_id": "bad id!"},
            {**self.base, "ph": 99},
            {**self.base, "ph": True},
            {**self.base, "ts": "yesterday"},
            {"schema": 1, "device_id": "tank-01"},           # no sensor value
            [],
        ]
        for payload in bad:
            clean, errors = schema.validate(payload)
            self.assertIsNone(clean)
            self.assertTrue(errors)

    def test_simulator_output_validates(self):
        for msg in simulator.generate_tank(3)[:20]:
            clean, errors = schema.validate(msg)
            self.assertEqual(errors, [])


class CalibrationTests(unittest.TestCase):
    def test_two_point(self):
        p = calibration.ph_two_point(2800, 2500)     # voltage falls as pH rises
        self.assertAlmostEqual(calibration.ph_from_mv(2800, p), 4.0)
        self.assertAlmostEqual(calibration.ph_from_mv(2500, p), 7.0)
        self.assertAlmostEqual(calibration.ph_from_mv(2650, p), 5.5)
        with self.assertRaises(ValueError):
            calibration.ph_two_point(2500, 2500)
        with self.assertRaises(ValueError):              # too close together to be a real fit
            calibration.ph_two_point(2500, 2510)

    def test_intervals_apply_until_next_calibration(self):
        t = [datetime(2026, 10, d, tzinfo=timezone.utc) for d in (1, 5, 9)]
        cals = [{"valid_from": t[2], "params": "c"}, {"valid_from": t[0], "params": "a"},
                {"valid_from": t[1], "params": "b"}]          # deliberately unordered
        self.assertEqual(calibration.intervals(cals),
                         [(t[0], t[1], "a"), (t[1], t[2], "b"), (t[2], None, "c")])
        self.assertEqual(calibration.intervals([]), [])


if __name__ == "__main__":
    unittest.main()
