"""Paired sensor + lab dataset: the table the outline's training step starts from.

One row per lab sample. The columns describe what the tank's sensors did in the hours BEFORE the sample was
collected (so nothing from after the sample leaks into the features), next to what the lab found. Whether
any feature predicts any lab result is the open research question; this module only builds the table.

The row builder is pure (no database, no Flask) so it can be tested and reused from a notebook.
"""
import csv
import io
import math
import statistics
from datetime import timedelta

from . import detectors, features

WINDOW_H = 6            # the window just before collection that the summary columns describe
PRIOR_H = 66            # the stretch before that, used as the tank's own baseline (72 h in total)
METRICS = detectors.METRICS
DERIVED = ("turbidity_ntu", "tds_ppm", "ph")      # metrics the recovery / settling features are computed for


def _slope_per_hour(points):
    if len(points) < 3:
        return None
    xs = [t / 3600.0 for t, _ in points]
    ys = [v for _, v in points]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    return None if sxx == 0 else sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx


def _bin5(points):
    """[(epoch, value)] -> mean per 5-minute bucket, the same resolution the live detectors use."""
    buckets = {}
    for t, v in points:
        buckets.setdefault(int(t // 300), []).append(v)
    return [(k * 300.0, statistics.fmean(vs)) for k, vs in sorted(buckets.items())]


def _r(v, nd=3):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else round(v, nd)


def sample_row(sample, readings, events, alerts, window_h=WINDOW_H):
    """Flat dict for one lab sample.

    sample:   {device_id, collected_at (aware datetime), lab_name, sample_location, values: [{parameter,
              qualifier, value, unit}]}
    readings: dicts with an aware `ts` and any of METRICS, ascending, covering up to PRIOR_H + window_h before collection
    events:   [{ts, kind}] any time; alerts: [{created_at, severity, feedback}] water alerts, any time
    """
    t0 = sample["collected_at"]
    win_start = t0 - timedelta(hours=window_h)
    row = {"device_id": sample["device_id"], "collected_at": t0, "lab_name": sample.get("lab_name"),
           "sample_location": sample.get("sample_location")}

    before = [r for r in readings if r["ts"] <= t0]
    window = [r for r in before if r["ts"] > win_start]
    prior = [r for r in before if r["ts"] <= win_start]
    row["window_hours"] = window_h
    row["window_readings"] = len(window)
    row["window_span_min"] = round((window[-1]["ts"] - window[0]["ts"]).total_seconds() / 60) if len(window) > 1 else 0
    row["minutes_since_last_reading"] = round((t0 - before[-1]["ts"]).total_seconds() / 60) if before else None

    for m in METRICS:
        w = [(r["ts"].timestamp(), float(r[m])) for r in window if r.get(m) is not None]
        p = [float(r[m]) for r in prior if r.get(m) is not None]
        vals = [v for _, v in w]
        row[f"{m}_n"] = len(vals)
        row[f"{m}_median"] = _r(statistics.median(vals)) if vals else None
        row[f"{m}_min"] = _r(min(vals)) if vals else None
        row[f"{m}_max"] = _r(max(vals)) if vals else None
        row[f"{m}_std"] = _r(statistics.pstdev(vals)) if len(vals) > 1 else None
        row[f"{m}_last"] = _r(vals[-1]) if vals else None
        row[f"{m}_slope_per_h"] = _r(_slope_per_hour(w), 4)
        z = None
        if vals and len(p) >= 30:
            med = statistics.median(p)
            sig = max(statistics.median(abs(x - med) for x in p) * 1.4826, detectors.MIN_SIGMA[m])
            z = (statistics.median(vals) - med) / sig
        row[f"{m}_z_vs_prior"] = _r(z, 2)

    for m in DERIVED:
        pts = _bin5([(r["ts"].timestamp(), float(r[m])) for r in before if r.get(m) is not None])
        s = features.summarize(pts, m)
        row[f"{m}_excursion"] = bool(s)
        row[f"{m}_recovery_min"] = s["recovery_minutes"] if s else None
        row[f"{m}_recovered"] = s["recovered"] if s else None
        row[f"{m}_half_life_min"] = s["half_life_minutes"] if s else None

    prev = [e for e in events if e["ts"] <= t0]
    last = max(prev, key=lambda e: e["ts"]) if prev else None
    row["last_event_kind"] = last["kind"] if last else None
    row["minutes_since_last_event"] = round((t0 - last["ts"]).total_seconds() / 60) if last else None
    day = [a for a in alerts if t0 - timedelta(hours=24) < a["created_at"] <= t0]
    row["alerts_24h"] = len(day)
    row["red_alerts_24h"] = sum(a["severity"] == "red" for a in day)
    row["alerts_24h_confirmed"] = sum(a["feedback"] == "confirmed" for a in day)
    row["alerts_24h_false_alarm"] = sum(a["feedback"] == "false_alarm" for a in day)

    for v in sample["values"]:
        row[f"lab_{v['parameter']}"] = v["value"] if v["qualifier"] in ("=", "<", ">") else v["qualifier"]
        row[f"lab_{v['parameter']}_q"] = v["qualifier"]
        row[f"lab_{v['parameter']}_unit"] = v.get("unit")
    return row


def write_csv(rows, fh):
    """Rows may have different lab_* columns; the union is written, sensor columns first."""
    fixed, lab = [], []
    for r in rows:
        for k in r:
            bucket = lab if k.startswith("lab_") and k not in ("lab_name",) else fixed
            if k not in bucket:
                bucket.append(k)
    w = csv.DictWriter(fh, fieldnames=fixed + sorted(lab), extrasaction="ignore", restval="")
    w.writeheader()
    for r in rows:
        w.writerow({k: _cell(v) for k, v in r.items()})


def _cell(v):
    if v is None:
        return ""
    if hasattr(v, "astimezone"):
        return v.astimezone(__import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@"):
        return "'" + v
    return v


def to_csv(rows):
    buf = io.StringIO()
    write_csv(rows, buf)
    return buf.getvalue()


# ------------------------------------------------------------------ database side

def load_rows(conn, dataset, window_h=WINDOW_H):
    """Build the table for every lab sample in a dataset."""
    from . import db
    samples = db.all(conn, """
        select l.id, l.device_id, l.collected_at, l.lab_name, l.sample_location
        from lab_results l join tanks t using (device_id) where t.dataset = %s order by l.collected_at""", (dataset,))
    out = []
    span = timedelta(hours=PRIOR_H + window_h)
    for s in samples:
        s["values"] = db.all(conn, "select parameter, qualifier, value, unit from lab_values "
                                   "where lab_result_id = %s order by parameter", (s["id"],))
        readings = db.all(conn, "select ts, ph, turbidity_ntu, tds_ppm, temp_c, orp_mv from readings "
                                "where device_id = %s and ts > %s and ts <= %s order by ts",
                          (s["device_id"], s["collected_at"] - span, s["collected_at"]))
        events = db.all(conn, "select ts, kind from events where device_id = %s", (s["device_id"],))
        alerts = db.all(conn, "select created_at, severity, feedback from alerts "
                              "where device_id = %s and kind = 'water'", (s["device_id"],))
        out.append(sample_row(s, readings, events, alerts, window_h))
    return out


def load_alerts(conn, dataset):
    """Water alerts with the operator's verdict: labels for the detectors themselves."""
    from . import db
    rows = db.all(conn, """
        select a.id, a.device_id, a.created_at, a.severity, a.status, a.feedback, a.message_source, a.findings
        from alerts a join tanks t using (device_id)
        where t.dataset = %s and a.kind = 'water' order by a.created_at""", (dataset,))
    for r in rows:
        f = r.pop("findings") or []
        r["metrics"] = ";".join(sorted({x.get("metric", "") for x in f}))
        r["detectors"] = ";".join(sorted({x.get("detector", "") for x in f}))
    return rows
