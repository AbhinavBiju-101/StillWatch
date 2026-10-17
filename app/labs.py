"""Lab results: one header per sample, one row per measured parameter, with qualifiers and units.

A real report says things like "E. coli: absent", "Fluoride: <0.5 mg/L", "pH: 7.4". Those are stored as
(parameter, qualifier, value, unit). The old fixed columns on lab_results (ph, turbidity_ntu, tds_ppm,
bacteria) are still filled in from the matching parameters, so chart markers and exports keep working.
Pure parsing functions live here so they can be tested without a database.
"""
import re
from datetime import datetime, timezone

# slug -> (label, default unit). Anything matching PARAM_RE is accepted; these just fill in the form.
CATALOG = {
    "ph": ("pH", ""),
    "turbidity": ("Turbidity", "NTU"),
    "tds": ("TDS", "mg/L"),
    "conductivity": ("Conductivity", "uS/cm"),
    "free_chlorine": ("Free chlorine", "mg/L"),
    "total_coliform": ("Total coliform", "/100 mL"),
    "e_coli": ("E. coli", "/100 mL"),
    "hardness": ("Hardness", "mg/L as CaCO3"),
    "alkalinity": ("Alkalinity", "mg/L as CaCO3"),
    "chloride": ("Chloride", "mg/L"),
    "nitrate": ("Nitrate", "mg/L"),
    "fluoride": ("Fluoride", "mg/L"),
    "iron": ("Iron", "mg/L"),
    "arsenic": ("Arsenic", "mg/L"),
}
DEFAULT_ROWS = ("ph", "turbidity", "tds", "total_coliform")
BACTERIA_PARAMS = ("total_coliform", "e_coli")

PARAM_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
QUALIFIERS = ("=", "<", ">", "absent", "present")
MAX_VALUES = 40
MAX_FILE = 5 * 1024 * 1024
_MAGIC = ((b"%PDF-", "application/pdf", ".pdf"),
          (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
          (b"\xff\xd8\xff", "image/jpeg", ".jpg"))
_READING_RE = re.compile(r"^(<=|>=|<|>|=)?\s*(-?\d+(?:\.\d+)?)$")


def parse_reading(text):
    """'7.2' -> ('=', 7.2); '<1' -> ('<', 1.0); 'absent' -> ('absent', None). Raises ValueError."""
    t = (text or "").strip().lower()
    if t in ("absent", "present"):
        return t, None
    if t in ("nd", "not detected", "negative"):
        return "absent", None
    m = _READING_RE.match(t.replace(",", "."))
    if not m:
        raise ValueError(f"cannot read the value {text!r}: use a number, <1, >10, absent or present")
    q = (m.group(1) or "=")
    q = {"<=": "<", ">=": ">"}.get(q, q)       # "<=" is stored as "<": the report's limit of detection
    return q, float(m.group(2))


def parse_value(item):
    """One entry of a sample's value list -> {parameter, qualifier, value, unit}."""
    if not isinstance(item, dict):
        raise ValueError("each value must be an object")
    param = str(item.get("parameter", "")).strip().lower().replace(" ", "_")
    if not PARAM_RE.match(param):
        raise ValueError(f"parameter {param!r} must be lowercase letters, digits or _")
    if item.get("text") is not None:
        qualifier, value = parse_reading(str(item["text"]))
    else:
        qualifier = item.get("qualifier", "=")
        value = item.get("value")
        if qualifier not in QUALIFIERS:
            raise ValueError(f"qualifier must be one of {list(QUALIFIERS)}")
        if qualifier in ("absent", "present"):
            if value is not None:
                raise ValueError(f"{param}: '{qualifier}' takes no number")
        elif isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
            raise ValueError(f"{param}: a numeric value is required")
        else:
            value = float(value)
    unit = item.get("unit")
    if unit is None:
        unit = CATALOG.get(param, ("", ""))[1]
    unit = str(unit).strip()[:24] or None
    return {"parameter": param, "qualifier": qualifier, "value": value, "unit": unit}


def parse_sample(body):
    """API/JSON body -> clean sample dict. Raises ValueError with a message fit to show the caller."""
    device_id = body.get("device_id")
    if not isinstance(device_id, str) or not device_id:
        raise ValueError("device_id is required")
    try:
        collected = datetime.fromisoformat(str(body.get("collected_at", "")).replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("collected_at (sample collection time, not report date) must be ISO 8601")
    if collected.tzinfo is None:
        collected = collected.replace(tzinfo=timezone.utc)
    collected = collected.astimezone(timezone.utc)
    if collected > datetime.now(timezone.utc):
        raise ValueError("collected_at is in the future")
    items = body.get("values")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_VALUES:
        raise ValueError(f"values must be a list of 1 to {MAX_VALUES} parameters")
    values = [parse_value(i) for i in items]
    if len({v["parameter"] for v in values}) != len(values):
        raise ValueError("each parameter may appear only once per sample")
    out = {"device_id": device_id, "collected_at": collected, "values": values}
    for field, limit in (("lab_name", 120), ("sample_location", 120), ("note", 500)):
        v = body.get(field)
        if v is not None and not isinstance(v, str):
            raise ValueError(f"{field} must be text")
        out[field] = (v or "").strip()[:limit] or None
    return out


def legacy_columns(values):
    """The old fixed columns, derived from the parameter rows: (ph, turbidity_ntu, tds_ppm, bacteria)."""
    by = {v["parameter"]: v for v in values}

    def num(name):
        v = by.get(name)
        return v["value"] if v and v["qualifier"] == "=" else None

    bact = [by[p] for p in BACTERIA_PARAMS if p in by]
    if not bact:
        bacteria = "not_tested"
    elif any(b["qualifier"] == "present" or (b["qualifier"] in ("=", ">") and b["value"] > 0) for b in bact):
        bacteria = "present"
    else:
        bacteria = "absent"
    return num("ph"), num("turbidity"), num("tds"), bacteria


def save_sample(conn, s):
    from . import db
    ph, ntu, tds, bacteria = legacy_columns(s["values"])
    row = db.one(conn, "insert into lab_results (device_id, collected_at, ph, turbidity_ntu, tds_ppm, bacteria, "
                       "note, lab_name, sample_location) values (%s, %s, %s, %s, %s, %s, %s, %s, %s) returning id",
                 (s["device_id"], s["collected_at"], ph, ntu, tds, bacteria, s["note"], s["lab_name"],
                  s["sample_location"]))
    for v in s["values"]:
        db.run(conn, "insert into lab_values (lab_result_id, parameter, qualifier, value, unit) "
                     "values (%s, %s, %s, %s, %s)",
               (row["id"], v["parameter"], v["qualifier"], v["value"], v["unit"]))
    return row["id"]


def check_upload(filename, data):
    """Accept PDFs and report photos only, judged by their first bytes, not their name.
    Returns (content_type, safe_filename). Raises ValueError."""
    if not data:
        raise ValueError("the file is empty")
    if len(data) > MAX_FILE:
        raise ValueError(f"the file is larger than {MAX_FILE // (1024 * 1024)} MB")
    for magic, ctype, ext in _MAGIC:
        if data.startswith(magic):
            stem = re.sub(r"[^A-Za-z0-9._-]+", "_", (filename or "report").rsplit(".", 1)[0])[:60].strip("._") or "report"
            return ctype, stem + ext
    raise ValueError("only PDF, PNG or JPEG files can be attached")


def save_file(conn, lab_id, filename, ctype, data):
    from . import db
    row = db.one(conn, "insert into lab_files (lab_result_id, filename, content_type, size_bytes, data) "
                       "values (%s, %s, %s, %s, %s) returning id", (lab_id, filename, ctype, len(data), bytes(data)))
    return row["id"]
