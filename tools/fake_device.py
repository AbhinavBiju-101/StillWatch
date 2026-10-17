"""Pretend to be the ESP32: POST simulated readings to the API, oldest first.

Example (against a local server):
    python tools/fake_device.py --url http://localhost:5000 --token YOUR_DEVICE_TOKEN \
        --device-id tank-test-01 --event-minutes-ago 100

Readings are back-dated so the last one is "now". Re-running is safe: a reading
with an existing (device_id, ts) is ignored, not duplicated.

What the real node does that this can imitate:
  --batch 20            upload through /api/readings/batch, like the node's flash-queue backlog upload
  --outage-minutes 90   pretend the network was down for the last 90 minutes: send everything before the outage
                        live, then upload the missed readings as one backlog after a check-in
  --checkin             call /api/device/checkin first and print the settings / firmware offer the server sends
  --faults ph,orp       report these sensors as faulty in the telemetry
Use the node's own token (from `flask --app app register-device`) as --token; the shared DEVICE_TOKEN also works.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.simulator import generate_tank  # noqa: E402


def request(url, token, path, payload, retries=3):
    req = urllib.request.Request(
        url.rstrip("/") + path, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "X-Device-Token": token})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:     # long timeout: Render free tier wakes slowly
                return json.load(r)
        except urllib.error.HTTPError as e:
            return {"error": e.code, "body": e.read().decode()[:300]}
        except (urllib.error.URLError, TimeoutError):
            time.sleep(2 ** attempt)
    return {"error": "unreachable"}


def post(url, token, payload, retries=3):
    return request(url, token, "/api/readings", payload, retries)


def telemetry(args, queued=0):
    t = {"fw": "0.5.0-sim", "rssi": -58, "uptime_s": 3600, "queued": queued, "boot": "power_on"}
    if args.faults:
        t["faults"] = [f for f in args.faults.split(",") if f]
    return t


def send_batches(args, msgs):
    sent = stored = alerts = 0
    for i in range(0, len(msgs), args.batch):
        chunk = msgs[i:i + args.batch]
        body = {"schema": 1, "device_id": args.device_id, **telemetry(args, len(msgs) - i - len(chunk)),
                "readings": [{k: v for k, v in m.items() if k not in ("schema", "device_id", "fw")} for m in chunk]}
        res = request(args.url, args.token, "/api/readings/batch", body)
        if "error" in res:
            print(f"batch failed: {res}")
            return None
        sent += len(chunk)
        stored += res["stored"]
        if res.get("rejected"):
            print(f"  server rejected {len(res['rejected'])} reading(s): {res['rejected'][:2]}")
        if res.get("alert_id"):
            alerts += 1
            print(f"[{sent}/{len(msgs)}] ALERT opened (id {res['alert_id']})")
        time.sleep(args.delay)
    return sent, stored, alerts


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
    ap.add_argument("--batch", type=int, default=0, help="upload this many readings per request (max 200)")
    ap.add_argument("--outage-minutes", type=int, default=0, help="simulate a network outage this long, then a backlog upload")
    ap.add_argument("--checkin", action="store_true", help="call /api/device/checkin first")
    ap.add_argument("--faults", default="", help="comma list of faulty sensors to report, e.g. ph,orp")
    a = ap.parse_args()
    if a.batch > 200:
        ap.error("--batch is at most 200")

    if a.checkin:
        res = request(a.url, a.token, "/api/device/checkin", {"schema": 1, "device_id": a.device_id, **telemetry(a)})
        print("check-in:", json.dumps(res))
        if "error" in res:
            return 1

    msgs = generate_tank(a.seed, hours=a.hours, event_minutes_ago=a.event_minutes_ago, device_id=a.device_id)
    backlog = []
    if a.outage_minutes:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=a.outage_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
        backlog = [m for m in msgs if m["ts"] > cutoff]
        msgs = [m for m in msgs if m["ts"] <= cutoff]
        print(f"Simulated outage: {len(backlog)} reading(s) are held back and uploaded afterwards.")

    stored = alerts = 0
    for phase, batch in (("live", msgs), ("backlog", backlog)):
        if not batch:
            continue
        if phase == "backlog" or a.batch:
            res = send_batches(argparse.Namespace(**{**vars(a), "batch": a.batch or 20}), batch)
            if res is None:
                return 1
            stored += res[1]
            alerts += res[2]
            continue
        for i, m in enumerate(batch, 1):
            res = post(a.url, a.token, {**m, **telemetry(a)})
            if "error" in res:
                print(f"[{i}/{len(batch)}] failed: {res}")
                return 1
            stored += bool(res.get("stored"))
            if res.get("alert_id"):
                alerts += 1
                print(f"[{i}/{len(batch)}] ALERT opened (id {res['alert_id']}) at {m['ts']}")
            time.sleep(a.delay)
    print(f"Done: {len(msgs) + len(backlog)} sent, {stored} new, {alerts} alert(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
