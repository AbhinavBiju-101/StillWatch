"""Pretend to be the ESP32: POST simulated readings to the API, oldest first.

Example (against a local server):
    python tools/fake_device.py --url http://localhost:5000 --token YOUR_DEVICE_TOKEN \
        --device-id tank-test-01 --event-minutes-ago 100

Readings are back-dated so the last one is "now". Re-running is safe: a reading
with an existing (device_id, ts) is ignored, not duplicated.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.simulator import generate_tank  # noqa: E402


def post(url, token, payload, retries=3):
    req = urllib.request.Request(
        url.rstrip("/") + "/api/readings", data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "X-Device-Token": token})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:     # long timeout: Render free tier wakes slowly
                return json.load(r)
        except urllib.error.HTTPError as e:
            return {"error": e.code, "body": e.read().decode()[:200]}
        except (urllib.error.URLError, TimeoutError):
            time.sleep(2 ** attempt)
    return {"error": "unreachable"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--device-id", default="tank-test-01")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--event-minutes-ago", type=int, default=None,
                    help="start a simulated contamination-like event this long before now")
    ap.add_argument("--delay", type=float, default=0.05, help="seconds between posts")
    a = ap.parse_args()

    msgs = generate_tank(a.seed, hours=a.hours, event_minutes_ago=a.event_minutes_ago, device_id=a.device_id)
    stored = alerts = 0
    for i, m in enumerate(msgs, 1):
        res = post(a.url, a.token, m)
        if "error" in res:
            print(f"[{i}/{len(msgs)}] failed: {res}")
            return 1
        stored += bool(res.get("stored"))
        if res.get("alert_id"):
            alerts += 1
            print(f"[{i}/{len(msgs)}] ALERT opened (id {res['alert_id']}) at {m['ts']}")
        time.sleep(a.delay)
    print(f"Done: {len(msgs)} sent, {stored} new, {alerts} alert(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
