"""Business logic. Routes stay thin; everything that touches several tables lives here."""
import logging
import statistics
import threading
from datetime import datetime, timedelta, timezone

from . import calibration, db, detectors, llm, schema, simulator

log = logging.getLogger(__name__)
READING_COLS = ("ph", "ph_raw_mv", "turbidity_ntu", "turbidity_raw_mv",
                "tds_ppm", "temp_c", "orp_mv", "fw")
_RANK = {"yellow": 1, "red": 2}
OFFLINE_AFTER = timedelta(minutes=30)


# ---------------------------------------------------------------- ingestion

def ingest(conn, clean, cooldown_min):
    """Store one reading (idempotent) and run detection.
    Returns {"inserted": bool, "alert_id": int|None, "context": dict|None}."""
    device_id = clean["device_id"]
    db.run(conn, "insert into tanks (device_id, name, dataset) values (%s, %s, 'pilot') "
                 "on conflict (device_id) do nothing", (device_id, device_id))
    tank = db.one(conn, "select dataset from tanks where device_id = %s", (device_id,))
    if tank["dataset"] != "pilot":
        raise ValueError("device_id belongs to a demo tank")

    cols = ", ".join(READING_COLS)
    marks = ", ".join(["%s"] * (len(READING_COLS) + 2))
    row = db.one(conn,
                 f"insert into readings (device_id, ts, {cols}) values ({marks}) "
                 "on conflict (device_id, ts) do nothing returning id",
                 (device_id, clean["ts"], *[clean.get(c) for c in READING_COLS]))
    if row is None:
        return {"inserted": False, "alert_id": None, "context": None}
    alert_id, context = run_analysis(conn, device_id, cooldown_min)
    return {"inserted": True, "alert_id": alert_id, "context": context}


# ----------------------------------------------------------------- analysis

def load_series(conn, device_id):
    """Last 72 h resampled to 5-minute buckets in SQL, as detector input."""
    rows = db.all(conn, """
        select date_bin(interval '5 minutes', ts, timestamptz '2000-01-01') as b,
               avg(ph) as ph, avg(turbidity_ntu) as turbidity_ntu, avg(tds_ppm) as tds_ppm,
               avg(temp_c) as temp_c, avg(orp_mv) as orp_mv
        from readings
        where device_id = %s and ts > now() - interval '72 hours'
        group by b order by b desc limit 700""", (device_id,))
    rows.reverse()
    series = {m: [] for m in detectors.METRICS}
    for r in rows:
        for m in detectors.METRICS:
            if r[m] is not None:
                series[m].append((r["b"].timestamp(), float(r[m])))
    return series


def build_context(conn, device_id, findings, series):
    tank = db.one(conn, "select name from tanks where device_id = %s", (device_id,))
    events = db.all(conn, "select ts, kind, note from events where device_id = %s "
                          "and ts > now() - interval '48 hours' order by ts desc limit 5", (device_id,))
    lab = db.one(conn, "select collected_at, ph, turbidity_ntu, tds_ppm, bacteria from lab_results "
                       "where device_id = %s order by collected_at desc limit 1", (device_id,))
    latest, usual = {}, {}
    for m, pts in series.items():
        if pts:
            vals = [v for _, v in pts]
            latest[m] = round(vals[-1], 2)
            usual[m] = round(statistics.median(vals[:-detectors.RECENT_N] or vals), 2)
    return {
        "tank": tank["name"] if tank else device_id,
        "findings": [f.as_dict() for f in findings],
        "latest_values": latest,
        "usual_values_for_this_tank": usual,
        "events_logged_last_48h": [{"when": str(e["ts"]), "kind": e["kind"], "note": e["note"]} for e in events],
        "refill_logged_last_48h": any(e["kind"] == "refill" for e in events),
        "latest_lab_result": dict(lab) if lab else None,
    }


def run_analysis(conn, device_id, cooldown_min):
    """Detect, and open an alert if warranted. Returns (alert_id, context) or (None, None)."""
    series = load_series(conn, device_id)
    findings = detectors.analyze(series)
    sev = detectors.severity(findings)
    if sev is None:
        return None, None

    open_alerts = db.all(conn, """
        select severity from alerts where device_id = %s and status = 'open'
        and created_at > now() - make_interval(mins => %s)""", (device_id, cooldown_min))
    if any(_RANK[a["severity"]] >= _RANK[sev] for a in open_alerts):
        return None, None                       # already alerted at this level or higher

    from psycopg.types.json import Jsonb        # lazy: keeps tests runnable without psycopg
    row = db.one(conn, "insert into alerts (device_id, severity, findings, message) "
                       "values (%s, %s, %s, %s) returning id",
                 (device_id, sev, Jsonb([f.as_dict() for f in findings]),
                  detectors.template_message(findings)))
    return row["id"], build_context(conn, device_id, findings, series)


def start_interpretation(cfg, alert_id, context):
    """Ask the LLM to explain an alert in the background. Call AFTER the alert's
    transaction has committed. The template message stays if the LLM fails."""
    def work():
        text = llm.interpret(cfg, context)
        if text:
            with db.connection() as conn:
                db.run(conn, "update alerts set message = %s, message_source = 'llm' where id = %s",
                       (text, alert_id))
    threading.Thread(target=work, daemon=True).start()


# ------------------------------------------------------------------- views

def tank_overview(conn, dataset):
    rows = db.all(conn, """
        select t.device_id, t.name, lr.ts as last_ts, lr.ph, lr.turbidity_ntu, lr.tds_ppm,
               lr.temp_c, lr.orp_mv, al.severity as open_severity
        from tanks t
        left join lateral (select * from readings r where r.device_id = t.device_id
                           order by ts desc limit 1) lr on true
        left join lateral (select severity from alerts a where a.device_id = t.device_id
                           and a.status = 'open'
                           order by (severity = 'red') desc, created_at desc limit 1) al on true
        where t.dataset = %s order by t.name""", (dataset,))
    now = datetime.now(timezone.utc)
    for r in rows:
        if r["open_severity"]:
            r["status"] = r["open_severity"]
        elif r["last_ts"] is None:
            r["status"] = "waiting"
        elif dataset == "pilot" and now - r["last_ts"] > OFFLINE_AFTER:
            r["status"] = "offline"
        else:
            r["status"] = "green"
    return rows


# -------------------------------------------------------------------- demo

DEMO_TANKS = [  # device_id, display name, seed, minutes since a simulated event began
    ("tank-demo-hostel", "Hostel Block A (demo)", 1, 100),
    ("tank-demo-school", "School overhead tank (demo)", 2, None),
    ("tank-demo-apartment", "Apartment sump (demo)", 3, None),
]


def seed_demo(conn, cooldown_min=180):
    """Replace all demo data with a fresh simulated 24 hours ending now."""
    db.run(conn, "delete from tanks where dataset = 'demo'")
    end = datetime.now(timezone.utc)
    cols = ", ".join(READING_COLS)
    marks = ", ".join(["%s"] * (len(READING_COLS) + 2))
    for device_id, name, seed, event_ago in DEMO_TANKS:
        db.run(conn, "insert into tanks (device_id, name, dataset) values (%s, %s, 'demo')",
               (device_id, name))
        rows = []
        for msg in simulator.generate_tank(seed, end=end, event_minutes_ago=event_ago, device_id=device_id):
            clean, errors = schema.validate(msg)
            assert not errors, errors
            rows.append((device_id, clean["ts"], *[clean.get(c) for c in READING_COLS]))
        db.run_many(conn, f"insert into readings (device_id, ts, {cols}) values ({marks})", rows)

    db.run(conn, "insert into events (device_id, ts, kind, note) values (%s, %s, 'refill', %s)",
           ("tank-demo-school", end - timedelta(hours=6), "Refilled from municipal supply"))
    db.run(conn, "insert into events (device_id, ts, kind, note) values (%s, %s, 'cleaning', %s)",
           ("tank-demo-apartment", end - timedelta(hours=20), "Quarterly tank cleaning"))
    db.run(conn, "insert into lab_results (device_id, collected_at, ph, turbidity_ntu, tds_ppm, bacteria, note) "
                 "values (%s, %s, 7.3, 1.4, 305, 'absent', 'Sample sent to NABL lab (demo data)')",
           ("tank-demo-apartment", end - timedelta(hours=24)))
    for device_id, *_ in DEMO_TANKS:
        run_analysis(conn, device_id, cooldown_min)     # template message only: no LLM at seed time


def ensure_demo_fresh(conn, max_age_hours, cooldown_min=180):
    """Called when the demo user logs in so the demo always looks live."""
    row = db.one(conn, "select max(r.ts) as last from readings r join tanks t using (device_id) "
                       "where t.dataset = 'demo'")
    if row["last"] is None or datetime.now(timezone.utc) - row["last"] > timedelta(hours=max_age_hours):
        seed_demo(conn, cooldown_min)
        return True
    return False


# ------------------------------------------------------------- calibration

def add_ph_calibration(conn, device_id, mv_ph4, mv_ph7, valid_from):
    """Store a two-point pH calibration. Raises ValueError if the points are unusable."""
    from psycopg.types.json import Jsonb
    params = calibration.ph_two_point(mv_ph4, mv_ph7)
    params.update({"mv_ph4": mv_ph4, "mv_ph7": mv_ph7})
    db.run(conn, "insert into calibrations (device_id, sensor, method, params, valid_from) "
                 "values (%s, 'ph', 'two_point', %s, %s)", (device_id, Jsonb(params), valid_from))
    return params


def recompute_ph(conn, device_id):
    """Re-derive stored pH from raw mV using the calibration valid at each reading's time.
    Returns the number of readings updated."""
    cals = db.all(conn, "select params, valid_from from calibrations where device_id = %s "
                        "and sensor = 'ph' and method = 'two_point'", (device_id,))
    total = 0
    for start, end, p in calibration.intervals(cals):
        sql = ("update readings set ph = least(14, greatest(0, %s * ph_raw_mv + %s)) "
               "where device_id = %s and ph_raw_mv is not null and ts >= %s")
        args = [p["slope"], p["offset"], device_id, start]
        if end is not None:
            sql += " and ts < %s"
            args.append(end)
        total += db.run(conn, sql, args)
    return total
