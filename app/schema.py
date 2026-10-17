"""Validation for the versioned JSON message the device sends (schema 1).

Sensors that are not wired yet are simply omitted. Unknown fields are ignored so
newer firmware does not break an older backend.
"""
import re
from datetime import datetime, timedelta, timezone

DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# field -> (min, max)
NUMERIC = {
    "ph": (0, 14), "ph_raw_mv": (0, 5000),
    "turbidity_ntu": (0, 3000), "turbidity_raw_mv": (0, 5000),
    "tds_ppm": (0, 10000), "tds_raw_mv": (0, 5000),
    "temp_c": (-10, 100), "orp_mv": (-2000, 2000), "orp_raw_mv": (0, 5000),
}
SENSOR_FIELDS = ("ph", "turbidity_ntu", "tds_ppm", "temp_c", "orp_mv")

# Timestamps outside this window almost always mean a device clock that never synced.
MAX_FUTURE = timedelta(minutes=10)
MAX_AGE = timedelta(days=90)

# What a node may say about its own health. Stored on the device, not on each reading.
FAULTS = ("ph", "turbidity", "tds", "temp", "orp", "storage", "clock")
TELEMETRY_INT = {"rssi": (-127, 0), "uptime_s": (0, 10 * 365 * 86400), "queued": (0, 1_000_000)}
BOOT_RE = re.compile(r"^[A-Za-z0-9_ -]{1,24}$")
BATCH_MAX = 200


def _parse_ts(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def validate_telemetry(payload):
    """Optional self-reported health fields. Returns (dict, errors). Missing fields are simply absent."""
    out, errors = {}, []
    for field, (lo, hi) in TELEMETRY_INT.items():
        v = payload.get(field)
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or not lo <= v <= hi:
            errors.append(f"{field} must be a number in [{lo}, {hi}]")
        else:
            out[field] = int(v)
    boot = payload.get("boot")
    if boot is not None:
        if isinstance(boot, str) and BOOT_RE.match(boot):
            out["boot"] = boot
        else:
            errors.append("boot must be a short plain-text reset reason")
    faults = payload.get("faults")
    if faults is not None:
        if (isinstance(faults, list) and len(faults) <= len(FAULTS)
                and all(isinstance(f, str) and f in FAULTS for f in faults)):
            out["faults"] = sorted(set(faults))
        else:
            errors.append(f"faults must be a list drawn from {list(FAULTS)}")
    fw = payload.get("fw")
    if fw is not None:
        out["fw"] = str(fw)[:32]
    return out, errors


def validate(payload, now=None):
    """Returns (clean_dict, errors_list). clean is None when there are errors."""
    errors = []
    if not isinstance(payload, dict):
        return None, ["body must be a JSON object"]
    if payload.get("schema") != 1:
        errors.append("schema must be 1")

    device_id = payload.get("device_id")
    if not isinstance(device_id, str) or not DEVICE_ID_RE.match(device_id):
        errors.append("device_id must be 1-64 chars of letters, digits, _ or -")

    now = now or datetime.now(timezone.utc)
    ts = now.replace(microsecond=0)
    if payload.get("ts") is not None:
        try:
            ts = _parse_ts(str(payload["ts"]))
            if ts > now + MAX_FUTURE:
                errors.append("ts is in the future: check the device clock")
            elif ts < now - MAX_AGE:
                errors.append("ts is more than 90 days old: check the device clock")
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

    telemetry, terrors = validate_telemetry(payload)
    errors += terrors
    clean["fw"] = telemetry.get("fw")
    clean["telemetry"] = telemetry
    return (None, errors) if errors else (clean, [])


def validate_batch(payload, now=None):
    """Validate {"schema":1, "device_id":..., "readings":[{ts, ph, ...}, ...], <telemetry>}.

    Returns (device_id, cleans, rejected, telemetry, errors). `errors` is non-empty only when the
    envelope itself is unusable; single bad readings land in `rejected` as {"index", "errors"} so
    one bad row never blocks the rest of a backlog."""
    if not isinstance(payload, dict):
        return None, [], [], {}, ["body must be a JSON object"]
    errors = []
    if payload.get("schema") != 1:
        errors.append("schema must be 1")
    device_id = payload.get("device_id")
    if not isinstance(device_id, str) or not DEVICE_ID_RE.match(device_id):
        errors.append("device_id must be 1-64 chars of letters, digits, _ or -")
    items = payload.get("readings")
    if not isinstance(items, list) or not items:
        errors.append("readings must be a non-empty list")
    elif len(items) > BATCH_MAX:
        errors.append(f"at most {BATCH_MAX} readings per batch")
    telemetry, terrors = validate_telemetry(payload)
    errors += terrors
    if errors:
        return device_id, [], [], {}, errors

    cleans, rejected = [], []
    for i, item in enumerate(items):
        if not isinstance(item, dict) or item.get("ts") is None:
            rejected.append({"index": i, "errors": ["each reading needs its own ts"]})
            continue
        clean, errs = validate({**item, "schema": 1, "device_id": device_id}, now=now)
        if errs:
            rejected.append({"index": i, "errors": errs})
        else:
            clean["fw"] = clean["fw"] or telemetry.get("fw")
            cleans.append(clean)
    return device_id, cleans, rejected, telemetry, []
