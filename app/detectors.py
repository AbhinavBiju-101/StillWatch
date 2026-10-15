"""Statistical change detectors. Pure functions: no database, no Flask.

Input is a dict {metric: [(epoch_seconds, value), ...]} sorted by time, already
resampled to ~5 minute buckets (see service.load_series). Detectors compare the
most recent readings with the tank's OWN earlier behaviour, using median/MAD so a
past event does not distort the baseline.

Everything here describes *unusual change*, never safety.
"""
from dataclasses import dataclass, asdict
import statistics

METRICS = ("ph", "turbidity_ntu", "tds_ppm", "temp_c", "orp_mv")
LABELS = {"ph": "pH", "turbidity_ntu": "turbidity", "tds_ppm": "TDS",
          "temp_c": "temperature", "orp_mv": "ORP"}

# Smallest believable noise per metric, so a very quiet baseline cannot make
# the detectors hypersensitive.
MIN_SIGMA = {"ph": 0.05, "turbidity_ntu": 0.3, "tds_ppm": 8.0,
             "temp_c": 0.5, "orp_mv": 15.0}

# Hard limits (low, high). Placeholder values for the prototype: verify against
# the drinking-water standard you cite (e.g. BIS IS 10500) before relying on them.
LIMITS = {"ph": (6.5, 8.5), "turbidity_ntu": (None, 5.0), "tds_ppm": (None, 500.0)}

# Temperature has a natural daily cycle, so only the spike detector watches it
# until the temperature-linked analysis is built.
DETECTORS_FOR = {"temp_c": ("rate_spike",)}
DEFAULT_DETECTORS = ("baseline_departure", "ewma_drift", "rate_spike", "cusum")

RECENT_N = 6        # ~30 min at 5 min buckets
MIN_BASELINE = 30   # ~2.5 h of history before detectors switch on
MAX_BASELINE = 600  # ~2 days


@dataclass
class Finding:
    metric: str
    detector: str
    score: float
    detail: str

    def as_dict(self):
        return asdict(self)


def _baseline_stats(xs, metric):
    med = statistics.median(xs)
    mad = statistics.median(abs(x - med) for x in xs) * 1.4826
    return med, max(mad, MIN_SIGMA[metric])


def baseline_departure(values, metric, thr=4.0):
    base, recent = values[:-RECENT_N][-MAX_BASELINE:], values[-RECENT_N:]
    med, sig = _baseline_stats(base, metric)
    now = statistics.median(recent)
    z = (now - med) / sig
    if abs(z) > thr:
        return Finding(metric, "baseline_departure", round(z, 1),
                       f"{LABELS[metric]} is {now:.2f} vs its usual {med:.2f}")


def ewma_drift(values, metric, alpha=0.1, thr_sigma=1.5):
    base = values[:-RECENT_N][-MAX_BASELINE:]
    med, sig = _baseline_stats(base, metric)
    e = med
    for v in base[-100:] + values[-RECENT_N:]:
        e = alpha * v + (1 - alpha) * e
    z = (e - med) / sig
    if abs(z) > thr_sigma:
        return Finding(metric, "ewma_drift", round(z, 1),
                       f"{LABELS[metric]} is drifting away from its usual {med:.2f}")


def rate_spike(values, metric, k=3, thr=6.0):
    base = values[:-RECENT_N][-MAX_BASELINE:]
    deltas = [base[i] - base[i - k] for i in range(k, len(base))]
    if len(deltas) < 10:
        return None
    sig = max(statistics.median(abs(d) for d in deltas) * 1.4826, MIN_SIGMA[metric])
    d_now = values[-1] - values[-1 - k]
    z = d_now / sig
    if abs(z) > thr:
        return Finding(metric, "rate_spike", round(z, 1),
                       f"{LABELS[metric]} changed by {d_now:+.2f} in {k * 5} minutes")


def cusum(values, metric, drift=1.0, h=10.0, window=60):
    base = values[:-RECENT_N][-MAX_BASELINE:]
    med, sig = _baseline_stats(base, metric)
    s_hi = s_lo = 0.0
    for v in values[-window:]:
        z = (v - med) / sig
        s_hi = max(0.0, s_hi + z - drift)
        s_lo = max(0.0, s_lo - z - drift)
    s = s_hi if s_hi >= s_lo else -s_lo
    if abs(s) > h:
        return Finding(metric, "cusum", round(s, 1),
                       f"{LABELS[metric]} has shifted away from its usual behaviour")


_FUNCS = {"baseline_departure": baseline_departure, "ewma_drift": ewma_drift,
          "rate_spike": rate_spike, "cusum": cusum}


def check_limits(metric, value):
    lo, hi = LIMITS.get(metric, (None, None))
    if lo is not None and value < lo:
        return Finding(metric, "limit", round(value - lo, 2), f"{LABELS[metric]} {value:.2f} is below {lo}")
    if hi is not None and value > hi:
        return Finding(metric, "limit", round(value - hi, 2), f"{LABELS[metric]} {value:.2f} is above {hi}")


def analyze(series):
    """series: {metric: [(epoch, value), ...]} -> list[Finding]."""
    findings = []
    for metric in METRICS:
        values = [v for _, v in (series.get(metric) or [])]
        if not values:
            continue
        f = check_limits(metric, values[-1])
        if f:
            findings.append(f)
        if len(values) < RECENT_N + MIN_BASELINE:
            continue
        for name in DETECTORS_FOR.get(metric, DEFAULT_DETECTORS):
            f = _FUNCS[name](values, metric)
            if f:
                findings.append(f)
    return findings


def severity(findings):
    """None, 'yellow' or 'red'."""
    if not findings:
        return None
    metrics = {f.metric for f in findings}
    if (any(f.detector == "limit" for f in findings) or len(metrics) >= 2
            or max(abs(f.score) for f in findings) >= 8):
        return "red"
    return "yellow"


def template_message(findings):
    """Used when no LLM is available. Never claims the water is safe."""
    names = sorted({LABELS[f.metric] for f in findings})
    details = "; ".join(dict.fromkeys(f.detail for f in findings))
    return (f"Unusual change detected in {', '.join(names)}. {details}. "
            f"Inspect the tank and send a sample for lab testing.")
