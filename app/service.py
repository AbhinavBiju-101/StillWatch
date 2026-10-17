"""Business logic. Routes stay thin; everything that touches several tables lives here."""
import logging
import statistics
import threading
from datetime import datetime, timedelta, timezone

from . import calibration, db, detectors, devices, features, llm, schema, simulator

log = logging.getLogger(__name__)
READING_COLS = ("ph", "ph_raw_mv", "turbidity_ntu", "turbidity_raw_mv",
                "tds_ppm", "tds_raw_mv", "temp_c", "orp_mv", "orp_raw_mv", "fw")
_RANK = {"yellow": 1, "red": 2}
OFFLINE_AFTER = timedelta(minutes=30)


# ---------------------------------------------------------------- ingestion

def _ensure_pilot_tank(conn, device_id):
    db.run(conn, "insert into tanks (device_id, name, dataset) values (%s, %s, 'pilot') "
                 "on conflict (device_id) do nothing", (device_id, device_id))
    tank = db.one(conn, "select dataset from tanks where device_id = %s", (device_id,))
    if tank["dataset"] != "pilot":
        raise ValueError("device_id belongs to a demo tank")


def _store(conn, clean):
    """Insert one reading. False when (device_id, ts) already exists, which makes retries harmless."""
    cols = ", ".join(READING_COLS)
    marks = ", ".join(["%s"] * (len(READING_COLS) + 2))
    row = db.one(conn,
                 f"insert into readings (device_id, ts, {cols}) values ({marks}) "
                 "on conflict (device_id, ts) do nothing returning id",
                 (clean["device_id"], clean["ts"], *[clean.get(c) for c in READING_COLS]))
    return row is not None


def ingest(conn, clean, cooldown_min):
    """Store one reading (idempotent) and run detection.
    Returns {"inserted": bool, "alert_id": int|None, "context": dict|None}."""
    device_id = clean["device_id"]
    _ensure_pilot_tank(conn, device_id)
    inserted = _store(conn, clean)
    devices.touch(conn, device_id, {**clean.get("telemetry", {}), "fw": clean.get("fw")})
    if not inserted:
        return {"inserted": False, "alert_id": None, "context": None}
    alert_id, context = run_analysis(conn, device_id, cooldown_min)
    return {"inserted": True, "alert_id": alert_id, "context": context}


def ingest_batch(conn, device_id, cleans, telemetry, cooldown_min):
    """Store a backlog from the node's flash queue. Detection runs once, after the whole batch,
    so uploading 200 old readings costs one analysis, not 200.
    Returns {"stored": int, "duplicates": int, "alert_id": int|None, "context": dict|None}."""
    _ensure_pilot_tank(conn, device_id)
    stored = sum(_store(conn, c) for c in cleans)
    devices.touch(conn, device_id, telemetry)
    alert_id = context = None
    if stored:
        alert_id, context = run_analysis(conn, device_id, cooldown_min)
    return {"stored": stored, "duplicates": len(cleans) - stored, "alert_id": alert_id, "context": context}


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
    """Everything the LLM is allowed to know: the detectors' findings plus the tank's own history.
    "Memory" in the outline is just the events log and past alerts with their outcomes."""
    tank = db.one(conn, "select name, location, probe_note from tanks where device_id = %s", (device_id,))
    events = db.all(conn, "select ts, kind, note from events where device_id = %s "
                          "and ts > now() - interval '48 hours' order by ts desc limit 5", (device_id,))
    lab = db.one(conn, "select id, collected_at, ph, turbidity_ntu, tds_ppm, bacteria, sample_location "
                       "from lab_results where device_id = %s order by collected_at desc limit 1", (device_id,))
    lab_values = db.all(conn, "select parameter, qualifier, value, unit from lab_values where lab_result_id = %s "
                              "order by parameter", (lab["id"],)) if lab else []
    past = db.all(conn, "select created_at, severity, status, feedback, findings from alerts "
                        "where device_id = %s and kind = 'water' and created_at > now() - interval '90 days' "
                        "order by created_at desc offset 1 limit 5", (device_id,))
    dev = db.one(conn, "select faults, fw from devices where device_id = %s", (device_id,))

    latest, usual = {}, {}
    for m, pts in series.items():
        if pts:
            vals = [v for _, v in pts]
            latest[m] = round(vals[-1], 2)
            usual[m] = round(statistics.median(vals[:-detectors.RECENT_N] or vals), 2)

    derived = []
    for metric in ("turbidity_ntu", "tds_ppm", "ph"):
        r = features.summarize(series.get(metric, []), metric)
        if r:
            derived.append({"metric": metric, "usual": round(r["baseline"], 2), "peak": round(r["peak"], 2),
                            "minutes_since_it_began": round((series[metric][-1][0] - r["start_t"]) / 60),
                            "back_to_usual": r["recovered"], "half_life_minutes": r["half_life_minutes"]})
    if lab:
        lab = {k: v for k, v in dict(lab).items() if k != "id"}
        lab["values"] = [dict(v) for v in lab_values]
    return {
        "tank": tank["name"] if tank else device_id,
        "location": tank["location"] if tank else None,
        "probe_position": tank["probe_note"] if tank else None,
        "findings": [f.as_dict() for f in findings],
        "latest_values": latest,
        "usual_values_for_this_tank": usual,
        "events_logged_last_48h": [{"when": str(e["ts"]), "kind": e["kind"], "note": e["note"]} for e in events],
        "refill_logged_last_48h": any(e["kind"] == "refill" for e in events),
        "latest_lab_result": lab,
        "earlier_alerts_and_outcomes": [
            {"when": str(a["created_at"]), "severity": a["severity"],
             "outcome": a["feedback"] or ("still open" if a["status"] == "open" else "closed without feedback"),
             "metrics": sorted({f.get("metric") for f in (a["findings"] or []) if f.get("metric")})}
            for a in past],
        "how_the_change_has_played_out_experimental": derived,
        "sensors_reported_faulty": (dev or {}).get("faults") or [],
    }


def run_analysis(conn, device_id, cooldown_min):
    """Detect, and open an alert if warranted. Returns (alert_id, context) or (None, None)."""
    series = load_series(conn, device_id)
    findings = detectors.analyze(series)
    sev = detectors.severity(findings)
    if sev is None:
        return None, None

    open_alerts = db.all(conn, """
        select severity from alerts where device_id = %s and status = 'open' and kind = 'water'
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


# ------------------------------------------------------------------- devices

def device_silent_message(name, minutes):
    return (f"No data from {name} for {_dur(minutes)}. Check the device's power and WiFi, and that the "
            "probes are still in the water. This is about the device, not the water quality.")


def _dur(minutes):
    minutes = int(minutes)
    if minutes < 60:
        return f"{minutes} min"
    h, m = divmod(minutes, 60)
    return f"{h} h {m:02d} min" if m else f"{h} h"


def sweep_devices(conn, silent_min):
    """Open a "device offline" alert for every pilot node that has gone quiet and has none open.
    Returns [alert_id, ...] for the alerts it created. Safe to run as often as you like."""
    from psycopg.types.json import Jsonb
    rows = db.all(conn, """
        select d.device_id, t.name,
               (extract(epoch from now() - d.last_seen_at) / 60)::int as silent_min
        from devices d join tanks t using (device_id)
        where t.dataset = 'pilot' and not d.revoked and d.last_seen_at is not null
          and d.last_seen_at < now() - make_interval(mins => %s)
          and not exists (select 1 from alerts a where a.device_id = d.device_id
                          and a.kind = 'device' and a.status = 'open')""", (silent_min,))
    ids = []
    for r in rows:
        a = db.one(conn, "insert into alerts (device_id, kind, severity, findings, message) "
                         "values (%s, 'device', 'yellow', %s, %s) returning id",
                   (r["device_id"], Jsonb([{"metric": "device", "detector": "silent",
                                            "score": r["silent_min"],
                                            "detail": f"no contact for {_dur(r['silent_min'])}"}]),
                    device_silent_message(r["name"], r["silent_min"])))
        ids.append(a["id"])
    return ids


_last_sweep = [0.0]


def maybe_sweep(cfg, every_s=300):
    """Cheap opportunistic sweep for hosts with no scheduler: runs at most once per `every_s` per
    process, triggered by dashboard traffic. A real scheduler (cron, POST /api/sweep) is still better."""
    import time
    from . import notify
    if time.time() - _last_sweep[0] < every_s:
        return []
    _last_sweep[0] = time.time()
    with db.connection() as conn:
        ids = sweep_devices(conn, cfg["DEVICE_SILENT_MIN"])
    for alert_id in ids:
        notify.start_notifications(dict(cfg), alert_id)
    return ids


def tank_status(dataset, last_ts, open_severity, now=None):
    """green / yellow / red / waiting / offline. One definition shared by the list and detail pages."""
    now = now or datetime.now(timezone.utc)
    if open_severity:
        return open_severity
    if last_ts is None:
        return "waiting"
    if dataset == "pilot" and now - last_ts > OFFLINE_AFTER:
        return "offline"
    return "green"


# ------------------------------------------------------------------- views

def tank_overview(conn, dataset):
    rows = db.all(conn, """
        select t.device_id, t.name, lr.ts as last_ts, lr.ph, lr.turbidity_ntu, lr.tds_ppm,
               lr.temp_c, lr.orp_mv, al.severity as open_severity
        from tanks t
        left join lateral (select * from readings r where r.device_id = t.device_id
                           order by ts desc limit 1) lr on true
        left join lateral (select severity from alerts a where a.device_id = t.device_id
                           and a.status = 'open' and a.kind = 'water'
                           order by (severity = 'red') desc, created_at desc limit 1) al on true
        where t.dataset = %s order by t.name""", (dataset,))
    for r in rows:
        r["status"] = tank_status(dataset, r["last_ts"], r["open_severity"])
    return rows


def tank_sparks(conn, dataset):
    """Last 24 h of turbidity per tank in 30-minute buckets: {device_id: [values]}, for the overview sparklines."""
    rows = db.all(conn, """
        select r.device_id, date_bin(interval '30 minutes', r.ts, timestamptz '2000-01-01') as b,
               avg(r.turbidity_ntu) as v
        from readings r join tanks t on t.device_id = r.device_id
        where t.dataset = %s and r.ts > now() - interval '24 hours'
        group by r.device_id, b order by r.device_id, b""", (dataset,))
    out = {}
    for r in rows:
        if r["v"] is not None:
            out.setdefault(r["device_id"], []).append(round(float(r["v"]), 2))
    return out


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

def add_ph_calibration(conn, device_id, mv_ph4, mv_ph7, valid_from, buffer_temp_c=None):
    """Store a two-point pH calibration. `buffer_temp_c` (temperature of the buffers) switches on
    temperature compensation for readings that carry a temperature. Raises ValueError if unusable."""
    from psycopg.types.json import Jsonb
    params = calibration.ph_two_point(mv_ph4, mv_ph7)
    params.update({"mv_ph4": mv_ph4, "mv_ph7": mv_ph7})
    if buffer_temp_c is not None:
        if not -5 <= buffer_temp_c <= 60:
            raise ValueError("buffer temperature must be between -5 and 60 C")
        params["cal_temp_c"] = buffer_temp_c
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
        cal_t = p.get("cal_temp_c")
        if cal_t is None:
            expr, args = "%s * ph_raw_mv + %s", [p["slope"], p["offset"]]
        else:     # same formula as calibration.ph_from_mv, in SQL, for readings that have a temperature
            mv7 = (7.0 - p["offset"]) / p["slope"]
            expr = ("case when temp_c is null then %s * ph_raw_mv + %s "
                    "else 7 + %s * (ph_raw_mv - %s) * (%s + 273.15) / (temp_c + 273.15) end")
            args = [p["slope"], p["offset"], p["slope"], mv7, cal_t]
        sql = (f"update readings set ph = least(14, greatest(0, {expr})) "
               "where device_id = %s and ph_raw_mv is not null and ts >= %s")
        args += [device_id, start]
        if end is not None:
            sql += " and ts < %s"
            args.append(end)
        total += db.run(conn, sql, args)
    return total
