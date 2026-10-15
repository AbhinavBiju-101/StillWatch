"""Validation for the versioned JSON message the device sends (schema 1).

Sensors that are not wired yet are simply omitted. Unknown fields are ignored so
newer firmware does not break an older backend.
"""
import re
from datetime import datetime, timezone

DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# field -> (min, max)
NUMERIC = {
    "ph": (0, 14), "ph_raw_mv": (0, 5000),
    "turbidity_ntu": (0, 3000), "turbidity_raw_mv": (0, 5000),
    "tds_ppm": (0, 10000), "temp_c": (-10, 100), "orp_mv": (-2000, 2000),
}
SENSOR_FIELDS = ("ph", "turbidity_ntu", "tds_ppm", "temp_c", "orp_mv")


def _parse_ts(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def validate(payload):
    """Returns (clean_dict, errors_list). clean is None when there are errors."""
    errors = []
    if not isinstance(payload, dict):
        return None, ["body must be a JSON object"]
    if payload.get("schema") != 1:
        errors.append("schema must be 1")

    device_id = payload.get("device_id")
    if not isinstance(device_id, str) or not DEVICE_ID_RE.match(device_id):
        errors.append("device_id must be 1-64 chars of letters, digits, _ or -")

    ts = datetime.now(timezone.utc).replace(microsecond=0)
    if payload.get("ts") is not None:
        try:
            ts = _parse_ts(str(payload["ts"]))
        except ValueError:
            errors.append("ts must be ISO 8601, e.g. 2026-10-09T10:15:00Z")

    clean = {"device_id": device_id, "ts": ts}
    for field, (lo, hi) in NUMERIC.items():
        if payload.get(field) is None:
            continue
        v = payload[field]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
            errors.append(f"{field} must be a number")
        elif not lo <= v <= hi:
            errors.append(f"{field} out of range [{lo}, {hi}]")
        else:
            clean[field] = float(v)

    if not any(k in clean for k in SENSOR_FIELDS):
        errors.append("at least one sensor value is required")

    fw = payload.get("fw")
    clean["fw"] = str(fw)[:32] if fw is not None else None
    return (None, errors) if errors else (clean, [])
