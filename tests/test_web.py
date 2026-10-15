"""Route-level tests. The database is stubbed, so these check auth, CSRF,
validation and wiring, not SQL (run the real thing against Supabase for that)."""
import re
import unittest
from contextlib import contextmanager
from unittest import mock

from app import create_app

CFG = {"TESTING": True, "SECRET_KEY": "t", "DEVICE_TOKEN": "dev-token",
       "DEMO_PASSWORD": "demo-pw", "TESTING_PASSWORD": "test-pw", "DATABASE_URL": "x"}


@contextmanager
def fake_connection():
    yield object()


class WebTests(unittest.TestCase):
    def setUp(self):
        from app import auth
        auth._FAILS.clear()
        self.app = create_app(CFG)
        self.client = self.app.test_client()

    def csrf(self, path="/login"):
        html = self.client.get(path).get_data(as_text=True)
        return re.search(r'name="csrf_token" value="([0-9a-f]+)"', html).group(1)

    def login(self, user, pw):
        return self.client.post("/login", data={"username": user, "password": pw,
                                                "csrf_token": self.csrf()})

    # ---- API
    def test_health_needs_no_auth(self):
        self.assertEqual(self.client.get("/api/health").get_json(), {"ok": True})

    def test_post_requires_device_token(self):
        r = self.client.post("/api/readings", json={"schema": 1})
        self.assertEqual(r.status_code, 401)
        r = self.client.post("/api/readings", json={"schema": 1}, headers={"X-Device-Token": "nope"})
        self.assertEqual(r.status_code, 401)

    def test_post_rejects_bad_payload(self):
        r = self.client.post("/api/readings", json={"schema": 1, "device_id": "t1", "ph": 99},
                             headers={"X-Device-Token": "dev-token"})
        self.assertEqual(r.status_code, 400)
        self.assertTrue(r.get_json()["errors"])

    def test_post_stores_and_starts_llm_only_for_new_alerts(self):
        good = {"schema": 1, "device_id": "tank-1", "ts": "2026-10-09T10:15:00Z", "ph": 7.2}
        hdr = {"X-Device-Token": "dev-token"}
        with mock.patch("app.db.connection", fake_connection), \
             mock.patch("app.service.start_interpretation") as start, \
             mock.patch("app.service.ingest",
                        return_value={"inserted": True, "alert_id": 5, "context": {"k": 1}}):
            r = self.client.post("/api/readings", json=good, headers=hdr)
        self.assertEqual((r.status_code, r.get_json()["alert_id"]), (201, 5))
        start.assert_called_once()

        with mock.patch("app.db.connection", fake_connection), \
             mock.patch("app.service.start_interpretation") as start, \
             mock.patch("app.service.ingest",
                        return_value={"inserted": False, "alert_id": None, "context": None}):
            r = self.client.post("/api/readings", json=good, headers=hdr)
        self.assertEqual(r.status_code, 200)                 # duplicate retry: accepted, not stored
        start.assert_not_called()

    def test_demo_tank_id_conflict_is_409(self):
        good = {"schema": 1, "device_id": "tank-demo-hostel", "ph": 7.2}
        with mock.patch("app.db.connection", fake_connection), \
             mock.patch("app.service.ingest", side_effect=ValueError("device_id belongs to a demo tank")):
            r = self.client.post("/api/readings", json=good, headers={"X-Device-Token": "dev-token"})
        self.assertEqual(r.status_code, 409)

    # ---- web
    def test_pages_redirect_to_login_when_logged_out(self):
        for path in ("/", "/alerts", "/events", "/labs", "/tank/x"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 302, path)
            self.assertIn("/login", r.headers["Location"])

    def test_login_flow_and_dataset_binding(self):
        self.assertEqual(self.login("testing", "wrong").status_code, 200)       # stays on form
        with self.client.session_transaction() as s:
            self.assertNotIn("dataset", s)
        r = self.login("testing", "test-pw")
        self.assertEqual(r.status_code, 302)
        with self.client.session_transaction() as s:
            self.assertEqual(s["dataset"], "pilot")

    def test_demo_login_binds_demo_dataset(self):
        with mock.patch("app.db.connection", fake_connection), \
             mock.patch("app.service.ensure_demo_fresh") as fresh:
            self.login("demo", "demo-pw")
        fresh.assert_called_once()
        with self.client.session_transaction() as s:
            self.assertEqual(s["dataset"], "demo")

    def test_empty_password_disables_account(self):
        app = create_app({**CFG, "TESTING_PASSWORD": ""})
        c = app.test_client()
        html = c.get("/login").get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([0-9a-f]+)"', html).group(1)
        c.post("/login", data={"username": "testing", "password": "", "csrf_token": token})
        with c.session_transaction() as s:
            self.assertNotIn("dataset", s)

    def test_post_without_csrf_token_is_rejected(self):
        r = self.client.post("/login", data={"username": "demo", "password": "demo-pw"})
        self.assertEqual(r.status_code, 400)

    def test_login_rate_limit(self):
        for _ in range(5):
            self.assertEqual(self.login("testing", "wrong").status_code, 200)
        r = self.login("testing", "test-pw")                  # correct password, but locked out
        self.assertEqual(r.status_code, 429)
        with self.client.session_transaction() as s:
            self.assertNotIn("dataset", s)

    def logged_in(self):
        self.login("testing", "test-pw")
        return self.csrf("/login")        # a token that is valid for this session

    def test_calibration_requires_login(self):
        self.assertEqual(self.client.get("/tank/x/calibration").status_code, 302)

    def test_calibration_form_validation(self):
        tank = {"device_id": "t1", "name": "T", "dataset": "pilot"}
        tok = self.logged_in()
        with mock.patch("app.db.connection", fake_connection), mock.patch("app.db.one", return_value=tank), \
             mock.patch("app.service.add_ph_calibration") as add:
            bad = [{"mv_ph4": "", "mv_ph7": "2500"}, {"mv_ph4": "abc", "mv_ph7": "2500"},
                   {"mv_ph4": "9999", "mv_ph7": "2500"}]
            for form in bad:
                r = self.client.post("/tank/t1/calibration",
                                     data={"action": "add", "csrf_token": tok, **form})
                self.assertEqual(r.status_code, 400, form)
            add.assert_not_called()
            add.side_effect = ValueError("must differ by at least 20 mV")
            r = self.client.post("/tank/t1/calibration", data={
                "action": "add", "csrf_token": tok, "mv_ph4": "2500", "mv_ph7": "2505"})
            self.assertEqual(r.status_code, 400)
            add.side_effect = None
            add.return_value = {"slope": -0.01}
            r = self.client.post("/tank/t1/calibration", data={
                "action": "add", "csrf_token": tok, "mv_ph4": "2800", "mv_ph7": "2500"})
            self.assertEqual(r.status_code, 302)
            self.assertEqual(add.call_count, 2)      # the rejected-by-service call + the successful one

    def test_recompute_action_calls_service(self):
        tank = {"device_id": "t1", "name": "T", "dataset": "pilot"}
        tok = self.logged_in()
        with mock.patch("app.db.connection", fake_connection), mock.patch("app.db.one", return_value=tank), \
             mock.patch("app.service.recompute_ph", return_value=42) as rec:
            r = self.client.post("/tank/t1/calibration", data={"action": "recompute", "csrf_token": tok})
        self.assertEqual(r.status_code, 302)
        rec.assert_called_once()

    def test_rename_validation(self):
        tank = {"device_id": "t1", "name": "T", "dataset": "pilot"}
        tok = self.logged_in()
        with mock.patch("app.db.connection", fake_connection), mock.patch("app.db.one", return_value=tank), \
             mock.patch("app.db.run") as run:
            r = self.client.post("/tank/t1/rename", data={"name": "   ", "csrf_token": tok})
            self.assertEqual(r.status_code, 400)
            r = self.client.post("/tank/t1/rename", data={"name": "x" * 81, "csrf_token": tok})
            self.assertEqual(r.status_code, 400)
            r = self.client.post("/tank/t1/rename", data={"name": "Hostel B", "csrf_token": tok})
            self.assertEqual(r.status_code, 302)
            run.assert_called_once()

    def test_open_redirect_is_blocked(self):
        r = self.client.post("/login?next=//evil.example", data={
            "username": "testing", "password": "test-pw", "csrf_token": self.csrf()})
        self.assertNotIn("evil.example", r.headers["Location"])


if __name__ == "__main__":
    unittest.main()
