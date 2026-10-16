"""The multi-page website. Every query is scoped to the logged-in account's dataset."""
import logging
from datetime import datetime, timezone

from flask import (Blueprint, abort, current_app, flash, jsonify, redirect,
                   render_template, request, session, url_for)

from . import IST, auth, calibration, db, service
from .auth import login_required

bp = Blueprint("web", __name__)
log = logging.getLogger(__name__)

EVENT_KINDS = ("cleaning", "refill", "rain", "maintenance", "other")
BACTERIA = ("not_tested", "absent", "present")


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
    with db.connection() as conn:
        rows = service.tank_overview(conn, session["dataset"])
    return render_template("tanks.html", tanks=rows)


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
    return render_template("tank.html", tank=t, alerts=alerts, events=events, latest=latest)


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
    fmt = "%H:%M" if hours <= 24 else "%d %b %H:%M"
    out = {"labels": [r["b"].astimezone(IST).strftime(fmt) for r in rows]}
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
                                                   _parse_local(request.form.get("valid_from")))
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
    if feedback not in ("confirmed", "false_alarm"):
        abort(400)
    with db.connection() as conn:
        n = db.run(conn, "update alerts set status = 'closed', feedback = %s where id = %s and device_id in "
                         "(select device_id from tanks where dataset = %s)",
                   (feedback, alert_id, session["dataset"]))
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
    return render_template("events.html", tanks=tanks_, events=rows, kinds=EVENT_KINDS)


@bp.route("/labs", methods=["GET", "POST"])
@login_required
def labs():
    with db.connection() as conn:
        tanks_ = db.all(conn, "select device_id, name from tanks where dataset = %s order by name",
                        (session["dataset"],))
        if request.method == "POST":
            device_id, bacteria = request.form.get("device_id", ""), request.form.get("bacteria")
            _tank_or_404(conn, device_id)
            if bacteria not in BACTERIA:
                abort(400)
            db.run(conn, "insert into lab_results (device_id, collected_at, ph, turbidity_ntu, tds_ppm, "
                         "bacteria, note) values (%s, %s, %s, %s, %s, %s, %s)",
                   (device_id, _parse_local(request.form.get("collected_at")), _num("ph"),
                    _num("turbidity_ntu"), _num("tds_ppm"), bacteria,
                    request.form.get("note", "").strip()[:500] or None))
            flash("Lab result saved.")
            return redirect(url_for("web.labs"))
        rows = db.all(conn, "select l.*, t.name from lab_results l join tanks t using (device_id) "
                            "where t.dataset = %s order by l.collected_at desc limit 50",
                      (session["dataset"],))
    return render_template("labs.html", tanks=tanks_, labs=rows, bacteria=BACTERIA)
