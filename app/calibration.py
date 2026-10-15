"""Calibration maths. Raw millivolts are always stored, so these can be
re-applied later ("recalibrate retroactively")."""


def ph_two_point(mv_ph4, mv_ph7):
    """Straight-line fit through the pH 4 and pH 7 buffer readings (mV).
    The sign of the slope is handled automatically."""
    if abs(mv_ph4 - mv_ph7) < 20:
        raise ValueError("pH 4 and pH 7 readings must differ by at least 20 mV")
    slope = (7.0 - 4.0) / (mv_ph7 - mv_ph4)      # pH units per mV
    return {"slope": slope, "offset": 7.0 - slope * mv_ph7}


def ph_from_mv(mv, params):
    return params["slope"] * mv + params["offset"]


def intervals(calibrations):
    """calibrations: dicts with 'valid_from' and 'params', any order.
    Returns [(start, end_or_None, params)]: each calibration applies from its valid_from
    until the next one begins. Readings before the first calibration are left as they are."""
    ordered = sorted(calibrations, key=lambda c: c["valid_from"])
    out = []
    for i, c in enumerate(ordered):
        end = ordered[i + 1]["valid_from"] if i + 1 < len(ordered) else None
        out.append((c["valid_from"], end, c["params"]))
    return out
