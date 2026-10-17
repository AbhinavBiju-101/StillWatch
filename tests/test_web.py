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


def _recent_ts():
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")


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
        good = {"schema": 1, "device_id": "tank-1", "ts": _recent_ts(), "ph": 7.2}
        hdr = {"X-Device-Token": "dev-token"}
        with mock.patch("app.db.connection", fake_connection), \
             mock.patch("app.devices.shared_token_blocked", return_value=False), \
             mock.patch("app.service.start_interpretation") as start, \
             mock.patch("app.service.ingest",
                        return_value={"inserted": True, "alert_id": 5, "context": {"k": 1}}):
            r = self.client.post("/api/readings", json=good, headers=hdr)
        self.assertEqual((r.status_code, r.get_json()["alert_id"]), (201, 5))
        start.assert_called_once()

        with mock.patch("app.db.connection", fake_connection), \
             mock.patch("app.devices.shared_token_blocked", return_value=False), \
             mock.patch("app.service.start_interpretation") as start, \
             mock.patch("app.service.ingest",
                        return_value={"inserted": False, "alert_id": None, "context": None}):
            r = self.client.post("/api/readings", json=good, headers=hdr)
        self.assertEqual(r.status_code, 200)                 # duplicate retry: accepted, not stored
        start.assert_not_called()

    def test_demo_tank_id_conflict_is_409(self):
        good = {"schema": 1, "device_id": "tank-demo-hostel", "ph": 7.2}
        with mock.patch("app.db.connection", fake_connection), \
             mock.patch("app.devices.shared_token_blocked", return_value=False), \
             mock.patch("app.service.ingest", side_effect=ValueError("device_id belongs to a demo tank")):
            r = self.client.post("/api/readings", json=good, headers={"X-Device-Token": "dev-token"})
        self.assertEqual(r.status_code, 409)

    # ---- web
    def test_pages_redirect_to_login_when_logged_out(self):
        for path in ("/tanks", "/alerts", "/events", "/labs", "/tank/x"):
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

    # ---- landing + quick sign-in
    def test_landing_is_public_and_needs_no_database(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn("Open demo", html)
        self.assertIn("Open testing", html)
        self.assertIn("favicon.svg", html)
        self.assertNotRegex(html.lower(), r"water is safe\b(?!”)")   # only ever quoted as the thing we never say

    def test_quick_demo_signs_in_demo_dataset(self):
        tok = self.csrf("/")
        with mock.patch("app.db.connection", fake_connection), \
             mock.patch("app.service.ensure_demo_fresh") as fresh:
            r = self.client.post("/login/quick", data={"account": "demo", "csrf_token": tok})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/tanks"))
        fresh.assert_called_once()
        with self.client.session_transaction() as s:
            self.assertEqual(s["dataset"], "demo")

    def test_quick_testing_is_off_by_default(self):
        tok = self.csrf("/")
        r = self.client.post("/login/quick", data={"account": "testing", "csrf_token": tok})
        self.assertEqual(r.status_code, 403)
        with self.client.session_transaction() as s:
            self.assertNotIn("dataset", s)
        # and the landing page falls back to a link to the password form, not a one-click form
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("/login?user=testing", html)

    def test_quick_testing_when_enabled(self):
        app = create_app({**CFG, "QUICK_LOGIN_TESTING": True})
        c = app.test_client()
        html = c.get("/").get_data(as_text=True)
        tok = re.search(r'name="csrf_token" value="([0-9a-f]+)"', html).group(1)
        r = c.post("/login/quick", data={"account": "testing", "csrf_token": tok})
        self.assertEqual(r.status_code, 302)
        with c.session_transaction() as s:
            self.assertEqual(s["dataset"], "pilot")

    def test_quick_login_needs_csrf_and_known_account(self):
        self.assertEqual(self.client.post("/login/quick", data={"account": "demo"}).status_code, 400)
        tok = self.csrf("/")
        self.assertEqual(self.client.post("/login/quick", data={"account": "admin", "csrf_token": tok}).status_code, 403)

    def test_login_prefills_only_known_usernames(self):
        html = self.client.get("/login?user=testing").get_data(as_text=True)
        self.assertIn('value="testing"', html)
        html = self.client.get('/login?user="><script>alert(1)</script>').get_data(as_text=True)
        self.assertNotIn("<script>alert(1)", html)

    def test_logout_returns_to_landing(self):
        self.login("testing", "test-pw")
        r = self.client.post("/logout", data={"csrf_token": self.csrf("/login")})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["Location"], "/")

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
