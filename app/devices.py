"""The device registry: per-device tokens, what a node last said about itself, and what the
backend tells it back (periods, limits, firmware to install).

Tokens are random and high-entropy, so a plain SHA-256 is enough to store them. The token itself
is shown once, when it is created, and cannot be recovered afterwards, only replaced.
"""
import hashlib
import hmac
import secrets

from . import db, detectors

TOKEN_PREFIX = "sw_"


def new_token():
    return TOKEN_PREFIX + secrets.token_urlsafe(24)


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Identity:
    """Who a request is. `device` is the devices row for a per-device token, else None (shared token)."""
    def __init__(self, kind, device=None):
        self.kind, self.device = kind, device

    def allows(self, device_id):
        return self.device is None or self.device["device_id"] == device_id


def identify(cfg, token):
    """Token -> Identity, or None. The shared token is checked first so that the common legacy path
    needs no database lookup; a per-device token costs one indexed query."""
    if not token:
        return None
    shared = cfg.get("DEVICE_TOKEN")
    if shared and cfg.get("ALLOW_SHARED_DEVICE_TOKEN", True) and hmac.compare_digest(token.encode(), shared.encode()):
        return Identity("shared")
    if token.startswith(TOKEN_PREFIX):
        with db.connection() as conn:
            row = db.one(conn, "select * from devices where token_hash = %s and not revoked",
                         (hash_token(token),))
        if row:
            return Identity("device", row)
    return None


def shared_token_blocked(conn, device_id):
    """A node that has been given its own token can no longer be impersonated with the shared one."""
    return bool(db.one(conn, "select 1 from devices where device_id = %s and token_hash is not null",
                       (device_id,)))


# ------------------------------------------------------------------ registry

def register(conn, device_id, name=None):
    """Create (or re-key) a node. Returns the new token, shown once. The tank is created as a pilot tank."""
    tank = db.one(conn, "select dataset from tanks where device_id = %s", (device_id,))
    if tank and tank["dataset"] != "pilot":
        raise ValueError("that id belongs to a demo tank")
    db.run(conn, "insert into tanks (device_id, name, dataset) values (%s, %s, 'pilot') "
                 "on conflict (device_id) do nothing", (device_id, name or device_id))
    token = new_token()
    db.run(conn, "insert into devices (device_id, token_hash) values (%s, %s) "
                 "on conflict (device_id) do update set token_hash = excluded.token_hash, revoked = false",
           (device_id, hash_token(token)))
    return token


def revoke(conn, device_id):
    return db.run(conn, "update devices set revoked = true where device_id = %s", (device_id,))


# ----------------------------------------------------------------- telemetry

def touch(conn, device_id, telemetry=None):
    """Record contact from a node, and close any open "device offline" alert: it is back."""
    t = telemetry or {}
    db.run(conn, """
        insert into devices (device_id, last_seen_at, fw, rssi, uptime_s, queued, boot_reason, faults)
        values (%s, now(), %s, %s, %s, %s, %s, %s)
        on conflict (device_id) do update set
          last_seen_at = now(),
          fw = coalesce(excluded.fw, devices.fw),
          rssi = coalesce(excluded.rssi, devices.rssi),
          uptime_s = coalesce(excluded.uptime_s, devices.uptime_s),
          queued = coalesce(excluded.queued, devices.queued),
          boot_reason = coalesce(excluded.boot_reason, devices.boot_reason),
          faults = case when %s then excluded.faults else devices.faults end""",
           (device_id, t.get("fw"), t.get("rssi"), t.get("uptime_s"), t.get("queued"),
            t.get("boot"), _jsonb(t.get("faults") or []), "faults" in t))
    db.run(conn, "update alerts set status = 'closed' where device_id = %s and kind = 'device' "
                 "and status = 'open'", (device_id,))


def _jsonb(obj):
    from psycopg.types.json import Jsonb        # lazy: keeps tests runnable without psycopg
    return Jsonb(obj)


# -------------------------------------------------------------- what we tell the node

def settings_for(cfg, device):
    """The periods and local safety limits a node should use. Limits come from the same table the
    backend detectors use, so the two can never drift apart."""
    ph_lo, ph_hi = detectors.LIMITS["ph"]
    out = {
        "read_period_s": cfg.get("DEVICE_READ_PERIOD_S", 10),
        "send_period_s": cfg.get("DEVICE_SEND_PERIOD_S", 60),
        "ph_min": ph_lo, "ph_max": ph_hi,
        "turb_max_ntu": detectors.LIMITS["turbidity_ntu"][1],
    }
    for key, (lo, hi) in OVERRIDABLE.items():
        v = (device or {}).get("settings", {}).get(key) if device else None
        if isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi:
            out[key] = v
    return out


# Per-device overrides an operator may set, with the range the firmware will accept.
OVERRIDABLE = {"read_period_s": (2, 3600), "send_period_s": (10, 86400)}


def pending_update(conn, device_id, reported_fw):
    """The firmware image this node should install, or None. Only a release an administrator
    explicitly assigned to the device is ever offered."""
    row = db.one(conn, """
        select r.version, r.sha256, r.size_bytes from devices d
        join firmware_releases r on r.version = d.target_fw
        where d.device_id = %s""", (device_id,))
    if row and row["version"] != reported_fw:
        return row
    return None


# ------------------------------------------------------------------ firmware

def add_release(conn, version, image, notes=None):
    if not version or len(version) > 32 or not all(c.isalnum() or c in ".-_+" for c in version):
        raise ValueError("version must be short and use letters, digits, . - _ +")
    if not 64 * 1024 <= len(image) <= 4 * 1024 * 1024:
        raise ValueError("firmware image must be between 64 KB and 4 MB")
    if image[:1] != b"\xe9":
        raise ValueError("this does not look like an ESP32 app image (it should start with byte 0xE9)")
    digest = hashlib.sha256(image).hexdigest()
    db.run(conn, "insert into firmware_releases (version, sha256, size_bytes, notes, image) "
                 "values (%s, %s, %s, %s, %s) on conflict (version) do nothing",
           (version, digest, len(image), notes, bytes(image)))
    return digest


def assign(conn, device_id, version):
    """Point one node (or all pilot nodes with device_id None) at a release; version None clears it."""
    if version and not db.one(conn, "select 1 from firmware_releases where version = %s", (version,)):
        raise ValueError(f"no firmware release called {version}")
    if device_id is None:
        return db.run(conn, "update devices set target_fw = %s", (version,))
    return db.run(conn, "update devices set target_fw = %s where device_id = %s", (version, device_id))
