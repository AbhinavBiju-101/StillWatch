"""Derived features from one metric's time series. Pure functions: no database, no Flask.

These are the research-flavoured signals from the project outline: how long a metric takes to
return to normal after an excursion (recovery time) and how fast it falls while the tank sits
still (settling half-life). Whether they predict lab results is an open question that the pilot's
paired sensor + lab data has to answer, so the UI labels them experimental.
"""
import math
import statistics

from .detectors import MIN_SIGMA

Z_THRESHOLD = 4.0        # how far from the baseline counts as an excursion
SETTLE_N = 3             # consecutive in-range points that count as "recovered"


def _baseline(values, metric):
    med = statistics.median(values)
    mad = statistics.median(abs(v - med) for v in values) * 1.4826
    return med, max(mad, MIN_SIGMA.get(metric, 0.05))


def find_excursion(points, metric, baseline_frac=0.5, min_points=2):
    """Latest run of points far from the tank's own baseline.

    points: [(epoch_s, value), ...] sorted by time. The baseline is the median/MAD of the first
    `baseline_frac` of the series, so the excursion itself does not distort it.
    Returns None, or a dict with start/peak/end indexes and the baseline.
    """
    if len(points) < 20:
        return None
    n_base = max(10, int(len(points) * baseline_frac))
    med, sig = _baseline([v for _, v in points[:n_base]], metric)
    flags = [abs(v - med) / sig > Z_THRESHOLD for _, v in points]
    end = None
    for i in range(len(points) - 1, n_base - 1, -1):
        if flags[i]:
            end = i
            break
    if end is None:
        return None
    start = end
    while start > 0 and (flags[start - 1] or (start >= 2 and flags[start - 2])):   # tolerate one dip
        start -= 1
    if sum(flags[start:end + 1]) < min_points:
        return None
    seg = points[start:end + 1]
    sign = 1 if statistics.median(v for _, v in seg) >= med else -1
    peak = max(range(start, end + 1), key=lambda i: sign * points[i][1])
    return {"start": start, "peak": peak, "end": end, "baseline": med, "sigma": sig, "sign": sign}


def recovery(points, exc):
    """Minutes from the start of the excursion until the metric has been back within 2 sigma of
    baseline for SETTLE_N consecutive points. `recovered` is False while it is still elevated."""
    t0 = points[exc["start"]][0]
    band = 2.0 * exc["sigma"]
    run = 0
    for i in range(exc["peak"], len(points)):
        if abs(points[i][1] - exc["baseline"]) <= band:
            run += 1
            if run >= SETTLE_N:
                t = points[i - SETTLE_N + 1][0]
                return {"recovered": True, "minutes": round((t - t0) / 60)}
        else:
            run = 0
    return {"recovered": False, "minutes": round((points[-1][0] - t0) / 60)}


def settling_half_life(points, exc):
    """Half-life in minutes of the excess over baseline after the peak, from an exponential fit
    (least squares on ln(excess) vs time). None when there are too few usable points, or the
    metric is not actually decaying."""
    base, peak_i = exc["baseline"], exc["peak"]
    peak_excess = abs(points[peak_i][1] - base)
    xs, ys = [], []
    for t, v in points[peak_i:]:
        e = exc["sign"] * (v - base)
        if e < 0.1 * peak_excess:
            break
        xs.append(t / 60.0)
        ys.append(math.log(e))
    if len(xs) < 4:
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx     # per minute
    if slope >= 0:
        return None
    return round(math.log(2) / -slope)


def summarize(points, metric):
    """Everything the tank page shows for one metric, or None when nothing unusual happened."""
    exc = find_excursion(points, metric)
    if not exc:
        return None
    rec = recovery(points, exc)
    peak_t, peak_v = points[exc["peak"]]
    return {"metric": metric, "baseline": exc["baseline"], "peak": peak_v, "peak_t": peak_t,
            "start_t": points[exc["start"]][0], "recovered": rec["recovered"],
            "recovery_minutes": rec["minutes"], "half_life_minutes": settling_half_life(points, exc)}
