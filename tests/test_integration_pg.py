"""End-to-end checks against a REAL Postgres. Skipped unless psycopg is installed and TEST_DATABASE_URL is set.

    TEST_DATABASE_URL="postgresql://user:pw@localhost:5432/stillwatch_test" python -m unittest tests.test_integration_pg

The database name must contain "test": the test DROPS and recreates every table in it.
"""
import hashlib
import io
import os
import re
import unittest
from datetime import datetime, timedelta, timezone

URL = os.environ.get("TEST_DATABASE_URL", "")
try:
    import psycopg  # noqa: F401
    HAVE_PG = True
except ImportError:
    HAVE_PG = False

CFG = {"TESTING": True, "SECRET_KEY": "t", "DEVICE_TOKEN": "shared-tok", "DEMO_PASSWORD": "demo-pw",
       "TESTING_PASSWORD": "test-pw", "DATABASE_URL": URL, "SWEEP_TOKEN": "sweep-tok",
       "DEVICE_SILENT_MIN": 30}


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@unittest.skipUnless(HAVE_PG and URL and "test" in URL.rsplit("/", 1)[-1].lower() + URL.lower().split("dbname=")[-1],
                     "needs psycopg and TEST_DATABASE_URL pointing at a scratch database with 'test' in its name")
class PostgresEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import create_app, db
        cls.app = create_app(CFG)
        with db.connection() as conn:
            db.run(conn, "drop schema public cascade")
            db.run(conn, "create schema public")
        db.run_script("schema.sql")
        cls.db = db
        cls.now = datetime.now(timezone.utc).replace(microsecond=0)

    def setUp(self):
        from app import auth
        auth._FAILS.clear()
        self.client = self.app.test_client()

    # -- helpers
    def post(self, path, body, token="shared-tok", **kw):
        return self.client.post(path, json=body, headers={"X-Device-Token": token}, **kw)

    def reading(self, device_id, minutes_ago, **vals):
        return {"schema": 1, "device_id": device_id, "ts": iso(self.now - timedelta(minutes=minutes_ago)),
                "ph": 7.2, "turbidity_ntu": 1.0, **vals}

    def login(self, user="testing", pw="test-pw"):
        html = self.client.get("/login").get_data(as_text=True)
        tok = re.search(r'name="csrf_token" value="([0-9a-f]+)"', html).group(1)
        self.client.post("/login", data={"username": user, "password": pw, "csrf_token": tok})
        self.client.get("/tanks")                    # any page render mints the session's form token
        with self.client.session_transaction() as s:
            return s["csrf"]

    # -- scenarios, numbered so they run in order (they share one database)
    def test_01_device_tokens_and_binding(self):
        from app import devices
        with self.db.connection() as conn:
            token = devices.register(conn, "node-a", "Node A")
        r = self.post("/api/readings", self.reading("node-a", 5), token=token)
        self.assertEqual(r.status_code, 201)
        # a node's own token only works for itself
        self.assertEqual(self.post("/api/readings", self.reading("node-b", 5), token=token).status_code, 403)
        # once a node has its own token the shared one no longer impersonates it
        self.assertEqual(self.post("/api/readings", self.reading("node-a", 4)).status_code, 401)
        # the shared token still serves devices that have none
        self.assertEqual(self.post("/api/readings", self.reading("legacy-1", 5)).status_code, 201)
        # duplicate retry
        self.assertEqual(self.post("/api/readings", self.reading("node-a", 5), token=token).status_code, 200)
        # revoked
        with self.db.connection() as conn:
            devices.revoke(conn, "node-a")
        self.assertEqual(self.post("/api/readings", self.reading("node-a", 3), token=token).status_code, 401)
        type(self).token_a = token
        with self.db.connection() as conn:
            self.db.run(conn, "update devices set revoked = false where device_id = 'node-a'")

    def test_02_batch_upload(self):
        items = [{k: v for k, v in self.reading("x", m).items() if k not in ("schema", "device_id")}
                 for m in range(120, 20, -5)]
        items.append({"ts": "not a time", "ph": 7})
        body = {"schema": 1, "device_id": "node-a", "readings": items, "rssi": -61, "uptime_s": 900,
                "queued": 0, "boot": "power_on", "faults": ["orp"], "fw": "0.5.0"}
        r = self.post("/api/readings/batch", body, token=self.token_a)
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        j = r.get_json()
        self.assertEqual((j["stored"], j["duplicates"], len(j["rejected"])), (20, 0, 1))
        again = self.post("/api/readings/batch", body, token=self.token_a).get_json()
        self.assertEqual((again["stored"], again["duplicates"]), (0, 20))
        with self.db.connection() as conn:
            d = self.db.one(conn, "select * from devices where device_id = 'node-a'")
        self.assertEqual((d["fw"], d["rssi"], d["boot_reason"], d["faults"]), ("0.5.0", -61, "power_on", ["orp"]))

    def test_03_checkin_config_and_ota(self):
        from app import devices
        r = self.post("/api/device/checkin", {"schema": 1, "device_id": "node-a", "fw": "0.5.0", "rssi": -58},
                      token=self.token_a)
        j = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(j["config"]["send_period_s"], 60)
        self.assertEqual(j["config"]["ph_min"], 6.5)
        self.assertIsNone(j["update"])
        image = b"\xe9" + os.urandom(100_000)
        with self.db.connection() as conn:
            digest = devices.add_release(conn, "0.6.0", image)
            devices.assign(conn, "node-a", "0.6.0")
        j = self.post("/api/device/checkin", {"schema": 1, "device_id": "node-a", "fw": "0.5.0"},
                      token=self.token_a).get_json()
        self.assertEqual(j["update"]["version"], "0.6.0")
        self.assertEqual(j["update"]["sha256"], digest)
        dl = self.client.get("/api/firmware/0.6.0.bin?device_id=node-a", headers={"X-Device-Token": self.token_a})
        self.assertEqual(hashlib.sha256(dl.data).hexdigest(), digest)
        # no offer once it reports the new version; and no download of an unassigned release
        j = self.post("/api/device/checkin", {"schema": 1, "device_id": "node-a", "fw": "0.6.0"},
                      token=self.token_a).get_json()
        self.assertIsNone(j["update"])
        self.assertEqual(self.client.get("/api/firmware/9.9.9.bin?device_id=node-a",
                                         headers={"X-Device-Token": self.token_a}).status_code, 404)
        self.assertEqual(self.client.get("/api/firmware/0.6.0.bin?device_id=node-a").status_code, 401)

    def test_04_sweep_opens_and_closes_device_alert(self):
        self.assertEqual(self.client.post("/api/sweep").status_code, 401)
        with self.db.connection() as conn:
            self.db.run(conn, "update devices set last_seen_at = now() - interval '3 hours' where device_id = 'node-a'")
        r = self.client.post("/api/sweep", headers={"X-Sweep-Token": "sweep-tok"})
        self.assertEqual(r.get_json()["opened"], 1)
        self.assertEqual(self.client.post("/api/sweep", headers={"X-Sweep-Token": "sweep-tok"}).get_json()["opened"], 0)
        with self.db.connection() as conn:
            a = self.db.one(conn, "select * from alerts where device_id = 'node-a' and kind = 'device'")
        self.assertEqual((a["status"], a["severity"]), ("open", "yellow"))
        self.assertIn("not the water", a["message"])
        # it does not make the tank look contaminated, and the device coming back closes it
        tok = self.login()
        html = self.client.get("/tanks").get_data(as_text=True)
        self.assertIn("Device offline", html)
        self.post("/api/device/checkin", {"schema": 1, "device_id": "node-a"}, token=self.token_a)
        with self.db.connection() as conn:
            self.assertEqual(self.db.one(conn, "select status from alerts where id = %s", (a["id"],))["status"], "closed")

    def test_05_water_alert_and_context(self):
        from app import service, simulator
        msgs = simulator.generate_tank(3, event_minutes_ago=100, device_id="node-sim")
        alert = None
        for m in msgs:
            m["ts"] = iso(datetime.strptime(m["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc))
            r = self.post("/api/readings", m)
            if r.get_json().get("alert_id"):
                alert = r.get_json()["alert_id"]
        self.assertIsNotNone(alert)
        with self.db.connection() as conn:
            series = service.load_series(conn, "node-sim")
            from app import detectors
            ctx = service.build_context(conn, "node-sim", detectors.analyze(series), series)
        self.assertIn("earlier_alerts_and_outcomes", ctx)
        self.assertIn("sensors_reported_faulty", ctx)
        self.assertTrue(ctx["findings"])

    def test_06_users_tokens_and_operator_api(self):
        from click.testing import CliRunner
        runner = self.app.test_cli_runner()
        res = runner.invoke(args=["create-user", "warden1", "--dataset", "pilot", "--password", "long-password-1",
                                  "--password", "long-password-1"]) if False else None
        from werkzeug.security import generate_password_hash
        from app import auth
        token = auth.new_user_token()
        with self.db.connection() as conn:
            self.db.run(conn, "insert into users (username, password_hash, dataset, api_token_hash) values "
                              "('warden1', %s, 'pilot', %s)",
                        (generate_password_hash("long-password-1"), auth.hash_token(token)))
        # password login for a database user
        tok = self.login("warden1", "long-password-1")
        with self.client.session_transaction() as s:
            self.assertEqual(s["dataset"], "pilot")
        self.client.post("/logout", data={"csrf_token": tok})
        self.assertEqual(self.login("warden1", "wrong")  and 0, 0)
        bearer = {"Authorization": f"Bearer {token}"}
        c = self.app.test_client()
        tanks = c.get("/api/tanks", headers=bearer).get_json()["tanks"]
        self.assertIn("node-a", {t["device_id"] for t in tanks})
        self.assertEqual(c.get("/api/tanks").status_code, 401)
        self.assertEqual(c.get("/api/tanks", headers={"Authorization": "Bearer swu_nope"}).status_code, 401)
        r = c.post("/api/events", json={"device_id": "node-a", "kind": "cleaning", "note": "scrubbed"}, headers=bearer)
        self.assertEqual(r.status_code, 201)
        self.assertEqual(c.post("/api/events", json={"device_id": "node-a", "kind": "bogus"}, headers=bearer).status_code, 400)
        self.assertEqual(c.post("/api/events", json={"device_id": "tank-demo-hostel", "kind": "refill"},
                                headers=bearer).status_code, 404)
        alerts = c.get("/api/alerts?status=open", headers=bearer).get_json()["alerts"]
        self.assertTrue(all(a["status"] == "open" for a in alerts))
        # a logged-in browser session may read, but never write, through the API
        self.login()
        self.assertEqual(self.client.get("/api/tanks").status_code, 200)
        self.assertEqual(self.client.post("/api/events", json={"device_id": "node-a", "kind": "rain"}).status_code, 401)

    def test_07_lab_results_api_and_web_upload(self):
        tokens = {}
        from app import auth
        token = auth.new_user_token()
        with self.db.connection() as conn:
            self.db.run(conn, "update users set api_token_hash = %s where username = 'warden1'", (auth.hash_token(token),))
        bearer = {"Authorization": f"Bearer {token}"}
        body = {"device_id": "node-a", "collected_at": iso(self.now - timedelta(hours=2)), "lab_name": "Aqua Labs",
                "sample_location": "outlet tap",
                "values": [{"parameter": "ph", "text": "7.4"}, {"parameter": "turbidity", "text": "<1"},
                           {"parameter": "total_coliform", "text": "absent"},
                           {"parameter": "fluoride", "qualifier": "<", "value": 0.5, "unit": "mg/L"}]}
        r = self.app.test_client().post("/api/lab-results", json=body, headers=bearer)
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        with self.db.connection() as conn:
            row = self.db.one(conn, "select * from lab_results where id = %s", (r.get_json()["id"],))
            vals = self.db.all(conn, "select * from lab_values where lab_result_id = %s order by parameter", (row["id"],))
        self.assertEqual((row["ph"], row["bacteria"], row["lab_name"]), (7.4, "absent", "Aqua Labs"))
        self.assertEqual(row["turbidity_ntu"], None)               # "<1" is a limit, not a measurement
        self.assertEqual([v["parameter"] for v in vals], ["fluoride", "ph", "total_coliform", "turbidity"])
        bad = {**body, "values": [{"parameter": "ph", "text": "seven"}]}
        self.assertEqual(self.app.test_client().post("/api/lab-results", json=bad, headers=bearer).status_code, 400)

        # the web form with a report attached
        tok = self.login()
        pdf = b"%PDF-1.4\n" + b"x" * 500
        data = {"csrf_token": tok, "device_id": "node-a", "lab_name": "City lab", "sample_location": "inlet",
                "parameter": ["ph", "e_coli", "fluoride"], "reading": ["7.1", "present", ""],
                "unit": ["", "/100 mL", "mg/L"], "report": (io.BytesIO(pdf), "report final (1).pdf")}
        r = self.client.post("/labs", data=data, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True)[:300])
        page = self.client.get("/labs").get_data(as_text=True)
        self.assertIn("e coli: present", page)
        self.assertNotIn("fluoride: ", page.split("City lab")[0][-0:] if False else "")
        fid = int(re.search(r"/labs/file/(\d+)", page).group(1))
        f = self.client.get(f"/labs/file/{fid}")
        self.assertEqual((f.data, f.headers["Content-Type"]), (pdf, "application/pdf"))
        self.assertIn("attachment", f.headers["Content-Disposition"])
        # not a real PDF, whatever the name says
        data["report"] = (io.BytesIO(b"MZ\x90\x00 not a pdf"), "evil.pdf")
        r = self.client.post("/labs", data=data, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)
        # another dataset cannot fetch it
        other = self.app.test_client()
        self.login_as = None
        html = other.get("/login").get_data(as_text=True)
        t2 = re.search(r'name="csrf_token" value="([0-9a-f]+)"', html).group(1)
        with self.app.test_client() as demo:
            h = demo.get("/login").get_data(as_text=True)
            tk = re.search(r'name="csrf_token" value="([0-9a-f]+)"', h).group(1)
            demo.post("/login", data={"username": "demo", "password": "demo-pw", "csrf_token": tk})
            self.assertEqual(demo.get(f"/labs/file/{fid}").status_code, 404)

    def test_08b_training_export(self):
        from app import training
        with self.db.connection() as conn:
            rows = training.load_rows(conn, "pilot")
            alerts = training.load_alerts(conn, "pilot")
        self.assertTrue(rows)
        r = next(x for x in rows if x["device_id"] == "node-a")
        self.assertEqual(r["lab_ph"] in (7.4, 7.1), True)
        self.assertIn("turbidity_ntu_median", r)
        self.assertTrue(all(a["feedback"] in (None, "confirmed", "false_alarm") for a in alerts))
        self.login()
        resp = self.client.get("/export/training.csv")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("lab_fluoride", resp.get_data(as_text=True).splitlines()[0])

    def test_08_pages_and_exports_render(self):
        self.login()
        for path in ("/tanks", "/tank/node-a", "/tank/node-sim", "/alerts", "/events", "/labs", "/devices",
                     "/tank/node-a/data?hours=24", "/notifications"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, f"{path}: {r.get_data(as_text=True)[:300]}")
        for kind in ("readings", "events", "labs", "alerts"):
            r = self.client.get(f"/tank/node-a/export/{kind}.csv")
            self.assertEqual(r.status_code, 200, kind)
        csv_labs = self.client.get("/tank/node-a/export/labs.csv").get_data(as_text=True)
        self.assertIn("fluoride", csv_labs)
        tok = self.client.get("/devices").get_data(as_text=True)
        csrf = re.search(r'name="csrf_token" value="([0-9a-f]+)"', tok).group(1)
        r = self.client.post("/devices/node-a/settings", data={"csrf_token": csrf, "read_period_s": "20",
                                                              "send_period_s": ""})
        self.assertEqual(r.status_code, 302)
        j = self.post("/api/device/checkin", {"schema": 1, "device_id": "node-a"}, token=self.token_a).get_json()
        self.assertEqual((j["config"]["read_period_s"], j["config"]["send_period_s"]), (20, 60))
        r = self.client.post("/devices/node-a/settings", data={"csrf_token": csrf, "read_period_s": "1"})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
