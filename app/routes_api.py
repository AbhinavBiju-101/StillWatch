"""JSON API.

Device endpoints (token in the X-Device-Token header):
  POST /api/readings         one reading
  POST /api/readings/batch   a backlog from the node's flash queue (up to 200)
  POST /api/device/checkin   heartbeat; the reply carries periods, limits, server time and any firmware update
  GET  /api/firmware/<version>.bin
Operator endpoints (browser session, or "Authorization: Bearer <user api token>"; writes need the token):
  GET /api/tanks, /api/tanks/<id>, /api/alerts, /api/readings;  POST /api/events, /api/lab-results
Scheduler:
  POST /api/sweep            (X-Sweep-Token) flag devices that went silent
"""
import hmac
from datetime import datetime, timezone

from flask import Blueprint, Response, current_app, jsonify, request

from . import auth, db, devices, labs, notify, schema, service

bp = Blueprint("api", __name__, url_prefix="/api")
EVENT_KINDS = ("cleaning", "refill", "rain", "maintenance", "other")


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


@bp.get("/health")
def health():
    """No auth, no database: for Render's health check and keep-alive pings."""
    return jsonify(ok=True)


# ------------------------------------------------------------------ device side

def _device_identity():
    """Identity for this request, or a (response, status) error."""
    who = devices.identify(current_app.config, request.headers.get("X-Device-Token", ""))
    if who is None:
        return None, (jsonify(error="unauthorized"), 401)
    return who, None


def _bind(who, device_id, conn):
    """A node's own token only works for that node; the shared token stops working for a node that has its own."""
    if not who.allows(device_id):
        return jsonify(error="this token belongs to a different device"), 403
    if who.device is None and devices.shared_token_blocked(conn, device_id):
        return jsonify(error="this device has its own token; the shared token no longer works for it"), 401
    return None


def _after_commit(result):
    cfg = dict(current_app.config)
    if result.get("alert_id"):
        service.start_interpretation(cfg, result["alert_id"], result["context"])
        notify.start_notifications(cfg, result["alert_id"])


@bp.post("/readings")
def post_reading():
    who, err = _device_identity()
    if err:
        return err
    clean, errors = schema.validate(request.get_json(silent=True))
    if errors:
        return jsonify(errors=errors), 400
    try:
        with db.connection() as conn:
            bad = _bind(who, clean["device_id"], conn)
            if bad:
                return bad
            result = service.ingest(conn, clean, current_app.config["ALERT_COOLDOWN_MIN"])
    except ValueError as e:
        return jsonify(error=str(e)), 409
    _after_commit(result)           # committed: now it is safe to explain a new alert in the background
    return jsonify(ok=True, stored=result["inserted"], alert_id=result["alert_id"]), \
        (201 if result["inserted"] else 200)


@bp.post("/readings/batch")
def post_batch():
    """Backlog upload. 200/201 means "this request is finished, drop all of it from your queue":
    individual bad readings are reported in `rejected` but never block the rest."""
    who, err = _device_identity()
    if err:
        return err
    device_id, cleans, rejected, telemetry, errors = schema.validate_batch(request.get_json(silent=True))
    if errors:
        return jsonify(errors=errors), 400
    try:
        with db.connection() as conn:
            bad = _bind(who, device_id, conn)
            if bad:
                return bad
            result = service.ingest_batch(conn, device_id, cleans, telemetry,
                                          current_app.config["ALERT_COOLDOWN_MIN"])
    except ValueError as e:
        return jsonify(error=str(e)), 409
    _after_commit(result)
    return jsonify(ok=True, stored=result["stored"], duplicates=result["duplicates"],
                   rejected=rejected, alert_id=result["alert_id"]), (201 if result["stored"] else 200)


@bp.post("/device/checkin")
def checkin():
    """Heartbeat. Works even when every probe is faulty (a reading needs at least one sensor value; this does not)."""
    who, err = _device_identity()
    if err:
        return err
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or body.get("schema") != 1:
        return jsonify(errors=["schema must be 1"]), 400
    device_id = body.get("device_id")
    if not isinstance(device_id, str) or not schema.DEVICE_ID_RE.match(device_id):
        return jsonify(errors=["device_id must be 1-64 chars of letters, digits, _ or -"]), 400
    telemetry, errors = schema.validate_telemetry(body)
    if errors:
        return jsonify(errors=errors), 400
    cfg = current_app.config
    try:
        with db.connection() as conn:
            bad = _bind(who, device_id, conn)
            if bad:
                return bad
            service._ensure_pilot_tank(conn, device_id)
            devices.touch(conn, device_id, telemetry)
            dev = db.one(conn, "select settings from devices where device_id = %s", (device_id,))
            update = devices.pending_update(conn, device_id, telemetry.get("fw"))
    except ValueError as e:
        return jsonify(error=str(e)), 409
    out = {"ok": True, "server_time": _iso(datetime.now(timezone.utc)),
           "config": devices.settings_for(cfg, dev), "update": None}
    if update:
        out["update"] = {"version": update["version"], "sha256": update["sha256"],
                         "size": update["size_bytes"],
                         "path": f"/api/firmware/{update['version']}.bin"}
    return jsonify(out)


@bp.get("/firmware/<version>.bin")
def firmware(version):
    who, err = _device_identity()
    if err:
        return err
    device_id = request.args.get("device_id", "")
    with db.connection() as conn:
        bad = _bind(who, device_id, conn)
        if bad:
            return bad
        # Only the release an administrator assigned to this very device may be downloaded.
        row = db.one(conn, "select r.image, r.sha256 from devices d join firmware_releases r "
                           "on r.version = d.target_fw where d.device_id = %s and r.version = %s",
                     (device_id, version))
    if not row:
        return jsonify(error="no such update for this device"), 404
    return Response(bytes(row["image"]), mimetype="application/octet-stream",
                    headers={"X-Firmware-SHA256": row["sha256"], "Cache-Control": "no-store"})


@bp.post("/sweep")
def sweep():
    expected = current_app.config.get("SWEEP_TOKEN")
    sent = request.headers.get("X-Sweep-Token", "")
    if not expected or not hmac.compare_digest(sent.encode(), expected.encode()):
        return jsonify(error="unauthorized"), 401
    with db.connection() as conn:
        ids = service.sweep_devices(conn, current_app.config["DEVICE_SILENT_MIN"])
    for alert_id in ids:
        notify.start_notifications(dict(current_app.config), alert_id)
    return jsonify(ok=True, opened=len(ids))


# ----------------------------------------------------------------- operator side

def _operator(write=False):
    """(dataset, error_response). Reads accept a browser session or a user API token; writes need the
    token, so a logged-in browser can never be tricked into changing data by a cross-site request."""
    dataset = auth.api_dataset(write_only_token=write)
    if dataset is None:
        return None, (jsonify(error="unauthorized"), 401)
    return dataset, None


@bp.get("/readings")
def list_readings():
    """Recent readings for one tank. Device token => pilot tanks; operator => own dataset."""
    device_id = request.args.get("device_id", "")
    limit = min(max(request.args.get("limit", 100, type=int), 1), 1000)
    who = devices.identify(current_app.config, request.headers.get("X-Device-Token", ""))
    if who and not who.allows(device_id):
        return jsonify(error="this token belongs to a different device"), 403
    if who:
        dataset = "pilot"
    else:
        dataset, err = _operator()
        if err:
            return err
    with db.connection() as conn:
        if not db.one(conn, "select 1 from tanks where device_id = %s and dataset = %s",
                      (device_id, dataset)):
            return jsonify(error="unknown device_id"), 404
        rows = db.all(conn, "select ts, ph, ph_raw_mv, turbidity_ntu, turbidity_raw_mv, tds_ppm, tds_raw_mv, "
                            "temp_c, orp_mv, orp_raw_mv, fw from readings where device_id = %s "
                            "order by ts desc limit %s", (device_id, limit))
    for r in rows:
        r["ts"] = _iso(r["ts"])
    return jsonify(device_id=device_id, readings=rows)


@bp.get("/tanks")
def list_tanks():
    dataset, err = _operator()
    if err:
        return err
    with db.connection() as conn:
        rows = service.tank_overview(conn, dataset)
    return jsonify(tanks=[{
        "device_id": r["device_id"], "name": r["name"], "status": r["status"],
        "last_reading_at": _iso(r["last_ts"]),
        "latest": {k: r[k] for k in ("ph", "turbidity_ntu", "tds_ppm", "temp_c", "orp_mv")},
    } for r in rows])


@bp.get("/tanks/<device_id>")
def get_tank(device_id):
    dataset, err = _operator()
    if err:
        return err
    with db.connection() as conn:
        t = db.one(conn, "select device_id, name, location, probe_note from tanks "
                         "where device_id = %s and dataset = %s", (device_id, dataset))
        if not t:
            return jsonify(error="unknown device_id"), 404
        d = db.one(conn, "select last_seen_at, fw, rssi, uptime_s, queued, boot_reason, faults, target_fw "
                         "from devices where device_id = %s", (device_id,))
    if d:
        d["last_seen_at"] = _iso(d["last_seen_at"])
    return jsonify(tank=t, device=d)


@bp.get("/alerts")
def list_alerts():
    dataset, err = _operator()
    if err:
        return err
    status = request.args.get("status")
    if status not in (None, "open", "closed"):
        return jsonify(error="status must be open or closed"), 400
    with db.connection() as conn:
        rows = db.all(conn, """
            select a.id, a.device_id, t.name, a.kind, a.created_at, a.severity, a.status, a.feedback,
                   a.message, a.message_source, a.findings
            from alerts a join tanks t using (device_id)
            where t.dataset = %s and (%s::text is null or a.status = %s)
            order by a.created_at desc limit 200""", (dataset, status, status))
    for r in rows:
        r["created_at"] = _iso(r["created_at"])
    return jsonify(alerts=rows)


def _when(value):
    if value is None:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("when must be ISO 8601, e.g. 2026-10-09T10:15:00Z")
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


@bp.post("/events")
def post_event():
    dataset, err = _operator(write=True)
    if err:
        return err
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(errors=["body must be a JSON object"]), 400
    kind, note = body.get("kind"), body.get("note")
    try:
        ts = _when(body.get("when"))
    except ValueError as e:
        return jsonify(errors=[str(e)]), 400
    if kind not in EVENT_KINDS:
        return jsonify(errors=[f"kind must be one of {list(EVENT_KINDS)}"]), 400
    if note is not None and not isinstance(note, str):
        return jsonify(errors=["note must be text"]), 400
    with db.connection() as conn:
        if not db.one(conn, "select 1 from tanks where device_id = %s and dataset = %s",
                      (body.get("device_id"), dataset)):
            return jsonify(error="unknown device_id"), 404
        row = db.one(conn, "insert into events (device_id, ts, kind, note) values (%s, %s, %s, %s) returning id",
                     (body["device_id"], ts, kind, (note or "").strip()[:500] or None))
    return jsonify(ok=True, id=row["id"]), 201


@bp.post("/lab-results")
def post_lab_result():
    """JSON body: {device_id, collected_at, lab_name?, sample_location?, note?,
    values: [{parameter, qualifier?, value?, unit?}]}. See app/labs.py for the rules."""
    dataset, err = _operator(write=True)
    if err:
        return err
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(errors=["body must be a JSON object"]), 400
    try:
        sample = labs.parse_sample(body)
    except ValueError as e:
        return jsonify(errors=[str(e)]), 400
    with db.connection() as conn:
        if not db.one(conn, "select 1 from tanks where device_id = %s and dataset = %s",
                      (sample["device_id"], dataset)):
            return jsonify(error="unknown device_id"), 404
        lab_id = labs.save_sample(conn, sample)
    return jsonify(ok=True, id=lab_id), 201
