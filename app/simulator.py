"""Fake tank data in the same shape the real device sends.

Used for the demo account, for `tools/fake_device.py`, and for testing the
detectors without hardware. Raw-mV mappings are illustrative, not calibrated.
"""
import math
import random
from datetime import datetime, timedelta, timezone


def generate_tank(seed, end=None, hours=24, interval_min=5, event_minutes_ago=None,
                  device_id="tank-demo-01", fw="0.3.1"):
    """Oldest-first list of message dicts. If event_minutes_ago is set, a
    contamination-like event (turbidity up, pH down, TDS up) begins that long
    before `end`, then decays."""
    rng = random.Random(seed)
    end = end or datetime.now(timezone.utc)
    n = hours * 60 // interval_min
    t_event = end - timedelta(minutes=event_minutes_ago) if event_minutes_ago else None
    walk_ph = walk_turb = 0.0
    out = []
    for i in range(n):
        t = (end - timedelta(minutes=interval_min * (n - 1 - i))).replace(microsecond=0)
        hour = t.hour + t.minute / 60
        walk_ph = 0.99 * walk_ph + rng.gauss(0, 0.002)
        walk_turb = 0.99 * walk_turb + rng.gauss(0, 0.01)

        ph = 7.2 + walk_ph + rng.gauss(0, 0.03)
        turb = 1.5 + walk_turb + rng.gauss(0, 0.12)
        tds = 300 + rng.gauss(0, 3)
        temp = 28.5 + 1.5 * math.sin(2 * math.pi * (hour - 9) / 24) + rng.gauss(0, 0.1)

        if t_event and t >= t_event:
            dt = (t - t_event).total_seconds() / 60
            f = min(dt / 20, 1.0) * math.exp(-max(dt - 20, 0) / 90)
            turb += 6.0 * f
            ph -= 0.45 * f
            tds += 40 * f

        turb = max(turb, 0.2)
        out.append({
            "schema": 1, "device_id": device_id,
            "ts": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ph": round(ph, 2), "ph_raw_mv": round(2500 - (ph - 7) * 95),
            "turbidity_ntu": round(turb, 2),
            "turbidity_raw_mv": max(round(4200 - turb * 323), 0),
            "tds_ppm": round(tds), "temp_c": round(temp, 1), "fw": fw,
        })
    return out
