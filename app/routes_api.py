"""JSON API. The ESP32 only ever talks to POST /api/readings."""
from flask import Blueprint, current_app, jsonify, request, session

from . import auth, db, schema, service

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.get("/health")
def health():
    """No auth, no database: for Render's health check and keep-alive pings."""
    return jsonify(ok=True)


@bp.post("/readings")
def post_reading():
    if not auth.device_token_ok():
        return jsonify(error="unauthorized"), 401
    clean, errors = schema.validate(request.get_json(silent=True))
    if errors:
        return jsonify(errors=errors), 400

    cfg = current_app.config
    try:
        with db.connection() as conn:
            result = service.ingest(conn, clean, cfg["ALERT_COOLDOWN_MIN"])
    except ValueError as e:
        return jsonify(error=str(e)), 409

    # Committed: now it is safe to explain a new alert in the background.
    if result["alert_id"]:
        service.start_interpretation(dict(cfg), result["alert_id"], result["context"])
    return jsonify(ok=True, stored=result["inserted"], alert_id=result["alert_id"]), \
        (201 if result["inserted"] else 200)


@bp.get("/readings")
def list_readings():
    """Recent readings for one tank. Device token => pilot tanks; web session => own dataset."""
    device_id = request.args.get("device_id", "")
    limit = min(max(request.args.get("limit", 100, type=int), 1), 1000)
    if auth.device_token_ok():
        dataset = "pilot"
    elif "dataset" in session:
        dataset = session["dataset"]
    else:
        return jsonify(error="unauthorized"), 401

    with db.connection() as conn:
        if not db.one(conn, "select 1 from tanks where device_id = %s and dataset = %s",
                      (device_id, dataset)):
            return jsonify(error="unknown device_id"), 404
        rows = db.all(conn, "select ts, ph, ph_raw_mv, turbidity_ntu, turbidity_raw_mv, tds_ppm, "
                            "temp_c, orp_mv, fw from readings where device_id = %s "
                            "order by ts desc limit %s", (device_id, limit))
    for r in rows:
        r["ts"] = r["ts"].strftime("%Y-%m-%dT%H:%M:%SZ")
    return jsonify(device_id=device_id, readings=rows)
