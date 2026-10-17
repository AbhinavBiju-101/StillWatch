"""Public status page, CSV export and notification contacts. The database is stubbed."""
import re
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest import mock

from app import create_app

CFG = {"TESTING": True, "SECRET_KEY": "t", "DEVICE_TOKEN": "d", "DEMO_PASSWORD": "demo-pw",
       "TESTING_PASSWORD": "test-pw", "DATABASE_URL": "x"}


@contextmanager
def fake_connection():
    yield object()


class Base(unittest.TestCase):
    def setUp(self):
        from app import auth
        auth._FAILS.clear()
        self.client = create_app(CFG).test_client()

    def login(self, user="testing", pw="test-pw"):
        html = self.client.get("/login").get_data(as_text=True)
        tok = re.search(r'name="csrf_token" value="([0-9a-f]+)"', html).group(1)
        self.client.post("/login", data={"username": user, "password": pw, "csrf_token": tok})
        html = self.client.get("/login").get_data(as_text=True)
        with self.client.session_transaction() as s:
            return s["csrf"]


class PublicStatusTests(Base):
    TANK = {"name": "Hostel A", "location": "Block A terrace", "public_status": "checking",
            "public_note": "Cleaning on Monday", "public_updated_at": datetime.now(timezone.utc)}

    def get(self, row):
        with mock.patch("app.db.connection", fake_connection), mock.patch("app.db.one", return_value=row):
            return self.client.get("/s/abc")

    def test_unknown_or_disabled_token_is_404(self):
        self.assertEqual(self.get(None).status_code, 404)

    def test_page_needs_no_login_and_shows_only_what_the_operator_set(self):
        r = self.get(self.TANK)
        html = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn("The operator is checking this tank", html)
        self.assertIn("Cleaning on Monday", html)
        self.assertNotIn("NTU", html)
        self.assertNotRegex(html.lower(), r"water is safe\b(?!”)")
        self.assertEqual(r.headers["X-Robots-Tag"], "noindex")
        self.assertEqual(r.headers["Cache-Control"], "no-store")

    def test_old_status_warns_visitors(self):
        old = {**self.TANK, "public_updated_at": datetime.now(timezone.utc) - timedelta(days=9)}
        self.assertIn("may be out of date", self.get(old).get_data(as_text=True))

    def test_only_logged_in_operators_can_change_it(self):
        r = self.client.post("/tank/t1/public", data={"public_enabled": "on"})
        self.assertEqual(r.status_code, 400)             # no CSRF token

    def test_invalid_status_is_rejected(self):
        tok = self.login()
        tank = {"device_id": "t1", "name": "T", "dataset": "pilot", "public_token": None}
        with mock.patch("app.db.connection", fake_connection), mock.patch("app.db.one", return_value=tank):
            r = self.client.post("/tank/t1/public", data={"csrf_token": tok, "public_status": "safe"})
        self.assertEqual(r.status_code, 400)

    def test_enabling_creates_a_random_token(self):
        tok = self.login()
        tank = {"device_id": "t1", "name": "T", "dataset": "pilot", "public_token": None}
        with mock.patch("app.db.connection", fake_connection), mock.patch("app.db.one", return_value=tank), \
             mock.patch("app.db.run") as run:
            r = self.client.post("/tank/t1/public", data={"csrf_token": tok, "public_enabled": "on",
                                                          "public_status": "steady"})
        self.assertEqual(r.status_code, 302)
        args = run.call_args[0][2]
        self.assertTrue(args[0])                          # enabled
        self.assertGreaterEqual(len(args[1]), 10)         # token


class ExportTests(Base):
    def test_export_requires_login(self):
        self.assertEqual(self.client.get("/tank/t1/export/readings.csv").status_code, 302)

    def test_unknown_kind_is_404(self):
        self.login()
        tank = {"device_id": "t1", "name": "T", "dataset": "pilot"}
        with mock.patch("app.db.connection", fake_connection), mock.patch("app.db.one", return_value=tank):
            self.assertEqual(self.client.get("/tank/t1/export/passwords.csv").status_code, 404)

    def test_csv_has_header_utc_times_and_neutralises_formulas(self):
        self.login()
        tank = {"device_id": "t1", "name": "T", "dataset": "pilot"}
        rows = [{"ts": datetime(2026, 10, 9, 10, 15, tzinfo=timezone.utc), "kind": "cleaning", "note": "=HYPERLINK(1)"}]
        with mock.patch("app.db.connection", fake_connection), mock.patch("app.db.one", return_value=tank), \
             mock.patch("app.db.all", return_value=rows):
            r = self.client.get("/tank/t1/export/events.csv?days=7")
        text = r.get_data(as_text=True).splitlines()
        self.assertEqual(r.mimetype, "text/csv")
        self.assertEqual(text[0], "ts,kind,note")
        self.assertEqual(text[1], "2026-10-09T10:15:00Z,cleaning,'=HYPERLINK(1)")
        self.assertIn("attachment", r.headers["Content-Disposition"])


class ContactTests(Base):
    def test_demo_account_cannot_add_contacts(self):
        with mock.patch("app.db.connection", fake_connection), mock.patch("app.service.ensure_demo_fresh"):
            tok = self.login("demo", "demo-pw")
            r = self.client.post("/notifications", data={"csrf_token": tok, "name": "A", "phone": "+919876543210",
                                                         "channel": "sms"})
        self.assertEqual(r.status_code, 403)

    def test_phone_numbers_need_a_country_code(self):
        tok = self.login()
        with mock.patch("app.db.connection", fake_connection), mock.patch("app.db.run") as run:
            r = self.client.post("/notifications", data={"csrf_token": tok, "name": "A", "phone": "9876543210",
                                                         "channel": "sms", "min_severity": "yellow"})
        self.assertEqual(r.status_code, 400)
        run.assert_not_called()

    def test_valid_contact_is_stored(self):
        tok = self.login()
        with mock.patch("app.db.connection", fake_connection), mock.patch("app.db.run") as run:
            r = self.client.post("/notifications", data={"csrf_token": tok, "name": "Warden", "phone": "+91 98765-43210",
                                                         "channel": "whatsapp", "min_severity": "red"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(run.call_args[0][2], ("Warden", "whatsapp", "+919876543210", "red"))


if __name__ == "__main__":
    unittest.main()
