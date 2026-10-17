"""The multi-page website. Every query is scoped to the logged-in account's dataset."""
import csv
import io
import logging
import secrets
from datetime import datetime, timezone

from flask import (Blueprint, Response, abort, current_app, flash, jsonify, redirect,
                   render_template, request, session, url_for)

from . import IST, auth, calibration, db, devices, features, labs as labs_lib, notify, service
from .auth import login_required

bp = Blueprint("web", __name__)
log = logging.getLogger(__name__)

EVENT_KINDS = ("cleaning", "refill", "rain", "maintenance", "other")
BACTERIA = ("not_tested", "absent", "present")
PUBLIC_STATUSES = {
    "steady": "No unusual change reported",
    "checking": "The operator is checking this tank",
    "tested": "A lab test is in progress",
}
STALE_AFTER_DAYS = 7


@bp.before_request
def _csrf():
    if request.method == "POST":
        auth.check_csrf()


def _tank_or_404(conn, device_id):
    tank = db.one(conn, "select * from tanks where device_id = %s and dataset = %s",
                  (device_id, session["dataset"]))
    if not tank:
        abort(404)
    return tank


def _parse_local(value):
    """<input type=datetime-local> value, entered in IST -> aware UTC datetime. Blank = now."""
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(value).replace(tzinfo=IST).astimezone(timezone.utc)
    except ValueError:
        abort(400, "Invalid date/time")


def _num(name):
    raw = request.form.get(name, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        abort(400, f"{name} must be a number")


# -------------------------------------------------------------------- landing + login

@bp.get("/")
def landing():
    cfg = current_app.config
    return render_template("landing.html", quick_demo=cfg["QUICK_LOGIN_DEMO"],
                           quick_testing=cfg["QUICK_LOGIN_TESTING"],
                           logged_in="dataset" in session)


def _start_session(dataset):
    session.clear()
    session["dataset"] = dataset
    if dataset == "demo":                         # keep the demo looking live
        try:
            with db.connection() as conn:
                service.ensure_demo_fresh(conn, current_app.config["DEMO_REFRESH_HOURS"],
                                          current_app.config["ALERT_COOLDOWN_MIN"])
        except Exception:
            log.exception("Could not refresh demo data")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ip = request.remote_addr or "?"
        if auth.locked_out(ip):
            flash("Too many failed attempts. Try again in a few minutes.")
            return render_template("login.html", prefill="", **_quick_flags()), 429
        dataset = auth.check_login(request.form.get("username", "").strip().lower(),
                                   request.form.get("password", ""))
        if dataset:
            auth.clear_failures(ip)
            _start_session(dataset)
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("web.tanks"))
        auth.record_failure(ip)
        flash("Wrong username or password.")
    prefill = request.args.get("user", "")
    return render_template("login.html", prefill=prefill if prefill in auth.ACCOUNTS else "",
                           **_quick_flags())


def _quick_flags():
    cfg = current_app.config
    return {"quick_demo": cfg["QUICK_LOGIN_DEMO"], "quick_testing": cfg["QUICK_LOGIN_TESTING"]}


@bp.post("/login/quick")
def quick_login():
    """One-click sign-in. Each account must be switched on in the config."""
    cfg = current_app.config
    account = request.form.get("account")
    if account == "demo" and cfg["QUICK_LOGIN_DEMO"]:
        _start_session("demo")
    elif account == "testing" and cfg["QUICK_LOGIN_TESTING"]:
        _start_session("pilot")
    else:
        abort(403, "Quick sign-in is not enabled for that account.")
    return redirect(url_for("web.tanks"))


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("web.landing"))


# -------------------------------------------------------------------- pages

@bp.get("/tanks")
@login_required
def tanks():
    if session["dataset"] == "pilot":
        try:
            service.maybe_sweep(current_app.config)      # no scheduler on free hosts: dashboard traffic triggers it
        except Exception:
            log.exception("device sweep failed")
    with db.connection() as conn:
        rows = service.tank_overview(conn, session["dataset"])
        sparks = service.tank_sparks(conn, session["dataset"])
        open_alerts = db.all(conn, """
            select a.*, t.name from alerts a join tanks t using (device_id)
            where t.dataset = %s and a.status = 'open'
            order by (a.kind = 'water') desc, (a.severity = 'red') desc, a.created_at desc limit 5""",
                              (session["dataset"],))
    for r in rows:
        r["spark"] = sparks.get(r["device_id"], [])
    return render_template("tanks.html", tanks=rows, open_alerts=open_alerts)


@bp.get("/tank/<device_id>")
@login_required
def tank(device_id):
    with db.connection() as conn:
        t = _tank_or_404(conn, device_id)
        alerts = db.all(conn, "select * from alerts where device_id = %s order by created_at desc limit 10",
                        (device_id,))
        events = db.all(conn, "select * from events where device_id = %s order by ts desc limit 10",
                        (device_id,))
        latest = db.one(conn, "select * from readings where device_id = %s order by ts desc limit 1",
                        (device_id,))
        device = db.one(conn, "select last_seen_at, fw, rssi, uptime_s, queued, boot_reason, faults, "
                              "token_hash is not null as own_token from devices where device_id = %s", (device_id,))
    insights = []
    try:
        with db.connection() as conn:
            series = service.load_series(conn, device_id)
        for metric, label, unit in (("turbidity_ntu", "Turbidity", "NTU"), ("tds_ppm", "TDS", "ppm"), ("ph", "pH", "")):
            r = features.summarize(series.get(metric, []), metric)
            if r:
                r.update(label=label, unit=unit)
                insights.append(r)
    except Exception:
        log.exception("Could not compute insights for %s", device_id)
    open_water = [a for a in alerts if a["status"] == "open" and a["kind"] == "water"]
    status = service.tank_status(session["dataset"], latest["ts"] if latest else None,
                                 ("red" if any(a["severity"] == "red" for a in open_water) else "yellow")
                                 if open_water else None)
    public_url = (url_for("web.public_status", token=t["public_token"], _external=True)
                  if t.get("public_token") else None)
    return render_template("tank.html", tank=t, alerts=alerts, events=events, latest=latest, status=status,
                           insights=insights, public_url=public_url, public_statuses=PUBLIC_STATUSES,
                           device=device)


@bp.get("/tank/<device_id>/data")
@login_required
def tank_data(device_id):
    hours = min(max(request.args.get("hours", 24, type=int), 1), 168)
    with db.connection() as conn:
        _tank_or_404(conn, device_id)
        rows = db.all(conn, """
            select date_bin(interval '5 minutes', ts, timestamptz '2000-01-01') as b,
                   avg(ph) as ph, avg(turbidity_ntu) as turbidity_ntu,
                   avg(tds_ppm) as tds_ppm, avg(temp_c) as temp_c
            from readings where device_id = %s and ts > now() - make_interval(hours => %s)
            group by b order by b""", (device_id, hours))
        marks = db.all(conn, "select ts, kind from events where device_id = %s "
                             "and ts > now() - make_interval(hours => %s) order by ts", (device_id, hours))
        labs_ = db.all(conn, "select collected_at from lab_results where device_id = %s "
                             "and collected_at > now() - make_interval(hours => %s) order by collected_at",
                       (device_id, hours))
    fmt = "%H:%M" if hours <= 24 else "%d %b %H:%M"
    out = {"labels": [r["b"].astimezone(IST).strftime(fmt) for r in rows],
           "t": [int(r["b"].timestamp() * 1000) for r in rows],          # epoch ms, for the chart's time axis
           "events": [{"t": int(m["ts"].timestamp() * 1000), "kind": m["kind"]} for m in marks],
           "labs": [{"t": int(m["collected_at"].timestamp() * 1000)} for m in labs_]}
    for m in ("ph", "turbidity_ntu", "tds_ppm", "temp_c"):
        out[m] = [None if r[m] is None else round(float(r[m]), 2) for r in rows]
    return jsonify(out)


@bp.post("/tank/<device_id>/rename")
@login_required
def tank_rename(device_id):
    name = request.form.get("name", "").strip()
    if not 1 <= len(name) <= 80:
        abort(400, "Name must be 1-80 characters")
    with db.connection() as conn:
        _tank_or_404(conn, device_id)
        db.run(conn, "update tanks set name = %s where device_id = %s", (name, device_id))
    flash("Tank renamed.")
    return redirect(url_for("web.tank", device_id=device_id))


@bp.route("/tank/<device_id>/calibration", methods=["GET", "POST"])
@login_required
def tank_calibration(device_id):
    with db.connection() as conn:
        t = _tank_or_404(conn, device_id)
        if request.method == "POST":
            action = request.form.get("action")
            if action == "add":
                mv4, mv7 = _num("mv_ph4"), _num("mv_ph7")
                if mv4 is None or mv7 is None or not (0 <= mv4 <= 5000 and 0 <= mv7 <= 5000):
                    abort(400, "Enter both buffer readings in millivolts (0-5000)")
                try:
                    p = service.add_ph_calibration(conn, device_id, mv4, mv7,
                                                   _parse_local(request.form.get("valid_from")), _num("buffer_temp_c"))
                except ValueError as e:
                    abort(400, str(e))
                flash(f"Calibration saved (slope {p['slope']:.5f} pH/mV). "
                      "Use 'Recompute pH' to apply it to stored readings.")
            elif action == "recompute":
                n = service.recompute_ph(conn, device_id)
                flash(f"Recomputed pH for {n} stored readings from raw mV.")
            else:
                abort(400)
            return redirect(url_for("web.tank_calibration", device_id=device_id))
        cals = db.all(conn, "select * from calibrations where device_id = %s and sensor = 'ph' "
                            "order by valid_from desc", (device_id,))
        latest = db.one(conn, "select ts, ph_raw_mv from readings where device_id = %s "
                              "and ph_raw_mv is not null order by ts desc limit 1", (device_id,))
    return render_template("calibration.html", tank=t, cals=cals, latest=latest)


@bp.get("/alerts")
@login_required
def alerts():
    with db.connection() as conn:
        rows = db.all(conn, """
            select a.*, t.name from alerts a join tanks t using (device_id)
            where t.dataset = %s order by (a.status = 'open') desc, a.created_at desc limit 100""",
                      (session["dataset"],))
    return render_template("alerts.html", alerts=rows)


@bp.post("/alerts/<int:alert_id>/feedback")
@login_required
def alert_feedback(alert_id):
    feedback = request.form.get("feedback")
    if feedback not in ("confirmed", "false_alarm", "dismiss"):
        abort(400)
    # "Dismiss" is for device alerts only: they are not about water, so they are no training label.
    kind = "device" if feedback == "dismiss" else "water"
    with db.connection() as conn:
        n = db.run(conn, "update alerts set status = 'closed', feedback = %s where id = %s and kind = %s "
                         "and device_id in (select device_id from tanks where dataset = %s)",
                   (None if feedback == "dismiss" else feedback, alert_id, kind, session["dataset"]))
    if not n:
        abort(404)
    return redirect(request.referrer or url_for("web.alerts"))


@bp.route("/events", methods=["GET", "POST"])
@login_required
def events():
    with db.connection() as conn:
        tanks_ = db.all(conn, "select device_id, name from tanks where dataset = %s order by name",
                        (session["dataset"],))
        if request.method == "POST":
            device_id, kind = request.form.get("device_id", ""), request.form.get("kind")
            _tank_or_404(conn, device_id)
            if kind not in EVENT_KINDS:
                abort(400)
            db.run(conn, "insert into events (device_id, ts, kind, note) values (%s, %s, %s, %s)",
                   (device_id, _parse_local(request.form.get("when")), kind,
                    request.form.get("note", "").strip()[:500] or None))
            flash("Event logged.")
            return redirect(url_for("web.events"))
        rows = db.all(conn, "select e.*, t.name from events e join tanks t using (device_id) "
                            "where t.dataset = %s order by e.ts desc limit 50", (session["dataset"],))
    return render_template("events.html", tanks=tanks_, events=rows, kinds=EVENT_KINDS,
                           selected=request.args.get("tank", ""))


@bp.route("/labs", methods=["GET", "POST"])
@login_required
def labs():
    with db.connection() as conn:
        tanks_ = db.all(conn, "select device_id, name from tanks where dataset = %s order by name",
                        (session["dataset"],))
        if request.method == "POST":
            device_id = request.form.get("device_id", "")
            _tank_or_404(conn, device_id)
            values = []
            for param, reading, unit in zip(request.form.getlist("parameter"), request.form.getlist("reading"),
                                            request.form.getlist("unit")):
                if not reading.strip():
                    continue                     # an empty row means "not measured"
                try:
                    values.append(labs_lib.parse_value({"parameter": param, "text": reading, "unit": unit or None}))
                except ValueError as e:
                    abort(400, str(e))
            if not values:
                abort(400, "Enter at least one result")
            try:
                sample = labs_lib.parse_sample({
                    "device_id": device_id, "collected_at": _parse_local(request.form.get("collected_at")).isoformat(),
                    "lab_name": request.form.get("lab_name"), "sample_location": request.form.get("sample_location"),
                    "note": request.form.get("note"), "values": values})
            except ValueError as e:
                abort(400, str(e))
            upload = request.files.get("report")
            blob = ctype = fname = None
            if upload and upload.filename:
                blob = upload.read(labs_lib.MAX_FILE + 1)
                try:
                    ctype, fname = labs_lib.check_upload(upload.filename, blob)
                except ValueError as e:
                    abort(400, str(e))
            lab_id = labs_lib.save_sample(conn, sample)
            if blob:
                labs_lib.save_file(conn, lab_id, fname, ctype, blob)
            flash("Lab result saved." + (" Report attached." if blob else ""))
            return redirect(url_for("web.labs"))
        rows = db.all(conn, "select l.*, t.name from lab_results l join tanks t using (device_id) "
                            "where t.dataset = %s order by l.collected_at desc limit 50",
                      (session["dataset"],))
        ids = [r["id"] for r in rows]
        vals = db.all(conn, "select lab_result_id, parameter, qualifier, value, unit from lab_values "
                            "where lab_result_id = any(%s) order by parameter", (ids,)) if ids else []
        files = db.all(conn, "select id, lab_result_id, filename from lab_files "
                             "where lab_result_id = any(%s) order by id", (ids,)) if ids else []
    for r in rows:
        r["values"] = [v for v in vals if v["lab_result_id"] == r["id"]]
        r["files"] = [f for f in files if f["lab_result_id"] == r["id"]]
    return render_template("labs.html", tanks=tanks_, labs=rows, catalog=labs_lib.CATALOG,
                           default_rows=labs_lib.DEFAULT_ROWS, selected=request.args.get("tank", ""))


@bp.get("/labs/file/<int:file_id>")
@login_required
def lab_file(file_id):
    with db.connection() as conn:
        f = db.one(conn, "select f.filename, f.content_type, f.data from lab_files f "
                         "join lab_results l on l.id = f.lab_result_id join tanks t on t.device_id = l.device_id "
                         "where f.id = %s and t.dataset = %s", (file_id, session["dataset"]))
    if not f:
        abort(404)
    return Response(bytes(f["data"]), mimetype=f["content_type"],
                    headers={"Content-Disposition": f'attachment; filename="{f["filename"]}"',
                             "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})


@bp.get("/devices")
@login_required
def devices_page():
    with db.connection() as conn:
        rows = db.all(conn, """
            select t.device_id, t.name, d.last_seen_at, d.fw, d.rssi, d.uptime_s, d.queued, d.boot_reason,
                   d.faults, d.target_fw, d.revoked, d.settings, d.token_hash is not null as own_token
            from tanks t left join devices d using (device_id)
            where t.dataset = %s order by t.name""", (session["dataset"],))
    return render_template("devices.html", devices=rows, silent_min=current_app.config["DEVICE_SILENT_MIN"],
                           defaults=devices.settings_for(current_app.config, None))


@bp.post("/devices/<device_id>/settings")
@login_required
def device_settings(device_id):
    """Operator-adjustable periods. Blank = use the server default. Ranges are enforced again in settings_for."""
    from psycopg.types.json import Jsonb
    out = {}
    for key, (lo, hi) in devices.OVERRIDABLE.items():
        v = _num(key)
        if v is not None:
            if not lo <= v <= hi:
                abort(400, f"{key} must be between {lo} and {hi}")
            out[key] = int(v)
    with db.connection() as conn:
        _tank_or_404(conn, device_id)
        n = db.run(conn, "update devices set settings = %s where device_id = %s", (Jsonb(out), device_id))
    if not n:
        abort(404)
    flash("Device settings saved. The node picks them up at its next check-in.")
    return redirect(url_for("web.devices_page"))


# -------------------------------------------------------------------- tank profile, public page, export

@bp.post("/tank/<device_id>/profile")
@login_required
def tank_profile(device_id):
    location = request.form.get("location", "").strip()[:120] or None
    probe_note = request.form.get("probe_note", "").strip()[:300] or None
    with db.connection() as conn:
        _tank_or_404(conn, device_id)
        db.run(conn, "update tanks set location = %s, probe_note = %s where device_id = %s",
               (location, probe_note, device_id))
    flash("Tank details saved.")
    return redirect(url_for("web.tank", device_id=device_id))


@bp.post("/tank/<device_id>/public")
@login_required
def tank_public(device_id):
    """The operator decides what the public sees. Nothing here is generated from sensor data."""
    status = request.form.get("public_status", "steady")
    if status not in PUBLIC_STATUSES:
        abort(400)
    enabled = request.form.get("public_enabled") == "on"
    note = request.form.get("public_note", "").strip()[:240] or None
    with db.connection() as conn:
        t = _tank_or_404(conn, device_id)
        token = t.get("public_token") or secrets.token_urlsafe(9)
        db.run(conn, "update tanks set public_enabled = %s, public_token = %s, public_status = %s, "
                     "public_note = %s, public_updated_at = now() where device_id = %s",
               (enabled, token, status, note, device_id))
    flash("Public status page is on." if enabled else "Public status page is off.")
    return redirect(url_for("web.tank", device_id=device_id) + "#public")


@bp.get("/s/<token>")
def public_status(token):
    """Minimal page behind a tank's QR code. No login, no readings, no thresholds."""
    with db.connection() as conn:
        t = db.one(conn, "select name, location, public_status, public_note, public_updated_at "
                         "from tanks where public_token = %s and public_enabled", (token,))
    if not t:
        abort(404)
    age = datetime.now(timezone.utc) - t["public_updated_at"] if t["public_updated_at"] else None
    stale = age is None or age.days >= STALE_AFTER_DAYS
    resp = current_app.make_response(render_template(
        "public.html", tank=t, label=PUBLIC_STATUSES[t["public_status"]], stale=stale))
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp


@bp.get("/tank/<device_id>/qr")
@login_required
def tank_qr(device_id):
    with db.connection() as conn:
        t = _tank_or_404(conn, device_id)
    if not (t.get("public_enabled") and t.get("public_token")):
        flash("Turn on the public status page first.")
        return redirect(url_for("web.tank", device_id=device_id) + "#public")
    return render_template("qr.html", tank=t, url=url_for("web.public_status", token=t["public_token"], _external=True))


EXPORTS = {
    "readings": ("select ts, ph, ph_raw_mv, turbidity_ntu, turbidity_raw_mv, tds_ppm, tds_raw_mv, temp_c, "
                 "orp_mv, orp_raw_mv, fw from readings where device_id = %s "
                 "and ts > now() - make_interval(days => %s) order by ts",
                 ("ts", "ph", "ph_raw_mv", "turbidity_ntu", "turbidity_raw_mv", "tds_ppm", "tds_raw_mv",
                  "temp_c", "orp_mv", "orp_raw_mv", "fw")),
    "events": ("select ts, kind, note from events where device_id = %s "
               "and ts > now() - make_interval(days => %s) order by ts", ("ts", "kind", "note")),
    # One row per measured parameter, keyed by the sample's collection time.
    "labs": ("select l.collected_at, l.lab_name, l.sample_location, v.parameter, v.qualifier, v.value, v.unit, "
             "l.note from lab_results l join lab_values v on v.lab_result_id = l.id "
             "where l.device_id = %s and l.collected_at > now() - make_interval(days => %s) "
             "order by l.collected_at, v.parameter",
             ("collected_at", "lab_name", "sample_location", "parameter", "qualifier", "value", "unit", "note")),
    "alerts": ("select created_at, kind, severity, status, feedback, message_source, message from alerts "
               "where device_id = %s and created_at > now() - make_interval(days => %s) order by created_at",
               ("created_at", "kind", "severity", "status", "feedback", "message_source", "message")),
}


def _csv_cell(v):
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@"):
        return "'" + v                      # stop spreadsheets treating free text as a formula
    return "" if v is None else v


@bp.get("/tank/<device_id>/export/<kind>.csv")
@login_required
def tank_export(device_id, kind):
    """Raw + calibrated readings, events, lab results and alert feedback: the labelled dataset
    the outline's training step starts from. Timestamps are UTC."""
    if kind not in EXPORTS:
        abort(404)
    days = min(max(request.args.get("days", 30, type=int), 1), 365)
    sql, cols = EXPORTS[kind]
    with db.connection() as conn:
        _tank_or_404(conn, device_id)
        rows = db.all(conn, sql, (device_id, days))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow([_csv_cell(r[c]) for c in cols])
    name = f"{device_id}-{kind}-{days}d.csv"
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@bp.get("/export/training.csv")
@login_required
def export_training():
    """One row per lab sample: what the sensors did in the hours before it, next to what the lab found."""
    from . import training
    with db.connection() as conn:
        rows = training.load_rows(conn, session["dataset"])
    return Response(training.to_csv(rows), mimetype="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="stillwatch-lab-windows.csv"'})


# -------------------------------------------------------------------- notification contacts

@bp.route("/notifications", methods=["GET", "POST"])
@login_required
def notifications():
    dataset = session["dataset"]
    with db.connection() as conn:
        if request.method == "POST":
            if dataset != "pilot":
                abort(403, "Messages are never sent for simulated tanks.")
            name = request.form.get("name", "").strip()[:80]
            channel, sev = request.form.get("channel"), request.form.get("min_severity", "yellow")
            phone = request.form.get("phone", "").replace(" ", "").replace("-", "")
            if not name or channel not in ("sms", "whatsapp") or sev not in ("yellow", "red"):
                abort(400)
            if not notify.PHONE_RE.match(phone):
                abort(400, "Enter the number with its country code, like +919876543210")
            db.run(conn, "insert into contacts (dataset, name, channel, phone, min_severity) "
                         "values ('pilot', %s, %s, %s, %s)", (name, channel, phone, sev))
            flash("Contact added.")
            return redirect(url_for("web.notifications"))
        contacts = db.all(conn, "select * from contacts where dataset = %s order by created_at", (dataset,))
        recent = db.all(conn, """
            select n.created_at, n.channel, n.status, n.detail, c.name as contact, t.name as tank
            from notifications n left join contacts c on c.id = n.contact_id
            left join alerts a on a.id = n.alert_id left join tanks t on t.device_id = a.device_id
            where c.dataset = %s order by n.created_at desc limit 15""", (dataset,))
    return render_template("notifications.html", contacts=contacts, recent=recent,
                           provider_ready=bool(notify.configured(current_app.config)))


@bp.post("/notifications/<int:contact_id>/delete")
@login_required
def contact_delete(contact_id):
    with db.connection() as conn:
        n = db.run(conn, "delete from contacts where id = %s and dataset = %s", (contact_id, session["dataset"]))
    if not n:
        abort(404)
    flash("Contact removed.")
    return redirect(url_for("web.notifications"))


@bp.post("/notifications/<int:contact_id>/test")
@login_required
def contact_test(contact_id):
    if session["dataset"] != "pilot":
        abort(403, "Messages are never sent for simulated tanks.")
    with db.connection() as conn:
        c = db.one(conn, "select * from contacts where id = %s and dataset = 'pilot'", (contact_id,))
    if not c:
        abort(404)
    ok, detail = notify.send(current_app.config, c["channel"], c["phone"],
                             "StillWatch test message. If you can read this, alerts will reach you here.")
    flash(f"Test message sent to {c['name']}." if ok else f"Could not send to {c['name']}: {detail}")
    return redirect(url_for("web.notifications"))
