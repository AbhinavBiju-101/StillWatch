import math
import random
import unittest

from app import features, notify


def series(n=200, event_at=None, peak=6.0, half_life=40, noise=0.1, seed=1):
    rng = random.Random(seed)
    pts = []
    for i in range(n):
        v = 1.5 + rng.gauss(0, noise)
        if event_at is not None and i >= event_at:
            minutes = (i - event_at) * 5
            v += peak * math.exp(-minutes * math.log(2) / half_life)
        pts.append((1_700_000_000 + i * 300, v))
    return pts


class FeatureTests(unittest.TestCase):
    def test_quiet_series_has_no_excursion(self):
        self.assertIsNone(features.summarize(series(), "turbidity_ntu"))

    def test_too_short_series_is_ignored(self):
        self.assertIsNone(features.summarize(series(10, event_at=5), "turbidity_ntu"))

    def test_excursion_is_found_and_recovers(self):
        r = features.summarize(series(event_at=120), "turbidity_ntu")
        self.assertIsNotNone(r)
        self.assertTrue(r["recovered"])
        self.assertGreater(r["peak"], 5)
        self.assertAlmostEqual(r["baseline"], 1.5, delta=0.1)
        self.assertGreater(r["recovery_minutes"], 30)

    def test_half_life_matches_the_decay_that_made_the_data(self):
        r = features.summarize(series(event_at=120, half_life=40, noise=0.03), "turbidity_ntu")
        self.assertAlmostEqual(r["half_life_minutes"], 40, delta=10)

    def test_still_elevated_is_reported_as_not_recovered(self):
        pts = series(150)
        pts = pts[:140] + [(t, v + 5) for t, v in pts[140:]]
        r = features.summarize(pts, "turbidity_ntu")
        self.assertIsNotNone(r)
        self.assertFalse(r["recovered"])

    def test_a_drop_is_an_excursion_too(self):
        pts = series(150)
        pts = pts[:120] + [(t, v - 1.3) for t, v in pts[120:]]
        pts = [(t, max(v, 0)) for t, v in pts]
        r = features.summarize(pts, "turbidity_ntu")
        self.assertIsNotNone(r)
        self.assertLess(r["peak"], 1.0)


class NotifyTests(unittest.TestCase):
    CFG = {"NOTIFY_PROVIDER": "twilio", "TWILIO_ACCOUNT_SID": "AC1", "TWILIO_AUTH_TOKEN": "t",
           "TWILIO_SMS_FROM": "+15005550006", "TWILIO_WHATSAPP_FROM": "whatsapp:+14155238886"}

    def test_message_never_claims_safety_and_links_to_the_tank(self):
        body = notify.build_message("Hostel A", "red", [{"detail": "Turbidity is 5.8 vs its usual 1.5"}],
                                    "https://x.example/", "tank-1")
        self.assertIn("Test recommended", body)
        self.assertIn("https://x.example/tank/tank-1", body)
        self.assertNotIn("safe", body.lower())

    def test_nothing_is_sent_when_not_configured(self):
        self.assertEqual(notify.send({"NOTIFY_PROVIDER": "none"}, "sms", "+919876543210", "hi")[0], False)

    def test_bad_phone_numbers_are_rejected_before_any_request(self):
        from unittest import mock
        with mock.patch("urllib.request.urlopen") as op:
            ok, detail = notify.send(self.CFG, "sms", "9876543210", "hi")
        self.assertFalse(ok)
        op.assert_not_called()

    def test_whatsapp_numbers_get_the_prefix(self):
        from unittest import mock
        resp = mock.MagicMock(); resp.status = 201
        resp.__enter__.return_value = resp
        with mock.patch("urllib.request.urlopen", return_value=resp) as op:
            ok, _ = notify.send(self.CFG, "whatsapp", "+919876543210", "hi")
        self.assertTrue(ok)
        sent = op.call_args[0][0].data.decode()
        self.assertIn("To=whatsapp%3A%2B919876543210", sent)
        self.assertIn("From=whatsapp%3A%2B14155238886", sent)

    def test_network_failure_is_reported_not_raised(self):
        from unittest import mock
        with mock.patch("urllib.request.urlopen", side_effect=OSError("down")):
            ok, detail = notify.send(self.CFG, "sms", "+919876543210", "hi")
        self.assertFalse(ok)
        self.assertEqual(detail, "OSError")


if __name__ == "__main__":
    unittest.main()
