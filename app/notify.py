"""SMS / WhatsApp alerts through Twilio's REST API, using only the standard library.

Messages are fixed templates built from an alert's findings, never free text from a model, and they
only ever say "unusual change detected, test recommended". Nothing is sent unless NOTIFY_PROVIDER
is "twilio" and the credentials are set, and never for the simulated demo dataset.
"""
import base64
import json
import logging
import re
import threading
import urllib.error
import urllib.parse
import urllib.request

from . import db

log = logging.getLogger(__name__)
PHONE_RE = re.compile(r"^\+[1-9]\d{7,14}$")
_RANK = {"yellow": 1, "red": 2}


def configured(cfg):
    return (cfg.get("NOTIFY_PROVIDER") == "twilio" and cfg.get("TWILIO_ACCOUNT_SID")
            and cfg.get("TWILIO_AUTH_TOKEN"))


def build_message(tank_name, severity, findings, base_url, device_id):
    lines = [f"StillWatch: unusual change detected at {tank_name}. Test recommended."]
    details = [f["detail"] for f in findings[:2] if f.get("detail")]
    if details:
        lines.append("; ".join(details) + ".")
    if severity == "red":
        lines.append("Several signals moved or one moved a lot. Please inspect the tank.")
    if base_url:
        lines.append(f"{base_url.rstrip('/')}/tank/{urllib.parse.quote(device_id)}")
    return " ".join(lines)[:600]


def build_device_message(tank_name, findings, base_url, device_id):
    """About the device only: it says nothing about the water."""
    mins = int((findings[0].get("score") if findings else 0) or 0)
    when = f"{mins} min" if mins < 60 else f"{mins // 60} h"
    lines = [f"StillWatch: no data from {tank_name} for {when}. Check the device's power and WiFi."]
    if base_url:
        lines.append(f"{base_url.rstrip('/')}/tank/{urllib.parse.quote(device_id)}")
    return " ".join(lines)[:600]


def send(cfg, channel, phone, body, timeout=10):
    """One message. Returns (ok, detail)."""
    if not configured(cfg):
        return False, "notifications are not configured"
    if not PHONE_RE.match(phone):
        return False, "phone number must look like +919876543210"
    sender = cfg.get("TWILIO_WHATSAPP_FROM") if channel == "whatsapp" else cfg.get("TWILIO_SMS_FROM")
    if not sender:
        return False, f"no sender number configured for {channel}"
    prefix = "whatsapp:" if channel == "whatsapp" else ""
    sid = cfg["TWILIO_ACCOUNT_SID"]
    data = urllib.parse.urlencode({"To": prefix + phone, "From": sender if not prefix or sender.startswith(prefix)
                                   else prefix + sender, "Body": body}).encode()
    req = urllib.request.Request(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json", data=data)
    token = base64.b64encode(f"{sid}:{cfg['TWILIO_AUTH_TOKEN']}".encode()).decode()
    req.add_header("Authorization", "Basic " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return (200 <= r.status < 300), f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode()).get("message", "")
        except Exception:
            msg = ""
        return False, f"HTTP {e.code} {msg}".strip()
    except Exception as e:                       # network down, DNS, timeout
        return False, type(e).__name__


def notify_alert(cfg, alert_id):
    """Send one alert to every pilot contact whose threshold it meets, and record each attempt."""
    if not configured(cfg):
        return 0
    with db.connection() as conn:
        a = db.one(conn, "select a.id, a.kind, a.severity, a.findings, t.name, t.device_id from alerts a "
                         "join tanks t using (device_id) where a.id = %s and t.dataset = 'pilot'", (alert_id,))
        if not a:
            return 0
        contacts = db.all(conn, "select * from contacts where dataset = 'pilot'")
        build = build_device_message if a["kind"] == "device" else None
        if build:
            body = build(a["name"], a["findings"] or [], cfg.get("PUBLIC_BASE_URL", ""), a["device_id"])
        else:
            body = build_message(a["name"], a["severity"], a["findings"] or [], cfg.get("PUBLIC_BASE_URL", ""),
                                 a["device_id"])
        sent = 0
        for c in contacts:
            if _RANK[a["severity"]] < _RANK[c["min_severity"]]:
                continue
            ok, detail = send(cfg, c["channel"], c["phone"], body)
            sent += ok
            db.run(conn, "insert into notifications (alert_id, contact_id, channel, status, detail) "
                         "values (%s, %s, %s, %s, %s)",
                   (alert_id, c["id"], c["channel"], "sent" if ok else "failed", detail))
    return sent


def start_notifications(cfg, alert_id):
    """Fire and forget, after the alert's transaction has committed."""
    def work():
        try:
            notify_alert(cfg, alert_id)
        except Exception:
            log.exception("Could not send notifications for alert %s", alert_id)
    threading.Thread(target=work, daemon=True).start()
