"""Accounts.
Two built-in accounts, configured by environment variables (an empty password disables one):
  demo    -> sees only the simulated 'demo' dataset
  testing -> sees only the real 'pilot' dataset (live device data)
Named operator accounts live in the `users` table (flask --app app create-user). Each has a dataset and
may hold an API token for scripts. Passwords are stored as salted scrypt hashes, tokens as SHA-256.
"""
import hashlib
import hmac
import logging
import secrets
import time
from functools import wraps

from flask import abort, current_app, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from . import db

log = logging.getLogger(__name__)
ACCOUNTS = {"demo": ("DEMO_PASSWORD", "demo"), "testing": ("TESTING_PASSWORD", "pilot")}
USER_TOKEN_PREFIX = "swu_"
# Checked when a username does not exist, so a missing user takes as long as a wrong password.
_DUMMY_HASH = generate_password_hash("not-a-real-password")


def check_login(username, password):
    """Returns the dataset name ('demo' / 'pilot') if credentials are valid, else None."""
    username = (username or "").strip().lower()
    entry = ACCOUNTS.get(username)
    if entry:
        expected = current_app.config[entry[0]]
        if expected and hmac.compare_digest(password.encode(), expected.encode()):
            return entry[1]
        return None
    try:
        with db.connection() as conn:
            row = db.one(conn, "select id, password_hash, dataset from users "
                               "where username = %s and not disabled", (username,))
            ok = check_password_hash(row["password_hash"] if row else _DUMMY_HASH, password)
            if row and ok:
                db.run(conn, "update users set last_login_at = now() where id = %s", (row["id"],))
                return row["dataset"]
    except Exception:                       # no database, no users table yet: only the built-in accounts exist
        log.debug("user lookup failed", exc_info=True)
    return None


def new_user_token():
    return USER_TOKEN_PREFIX + secrets.token_urlsafe(24)


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def api_dataset(write_only_token=False):
    """Dataset for an operator API call, or None. A bearer token always works; a browser session works
    for reads only (writes by cookie would be open to cross-site request forgery)."""
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        token = header[7:].strip()
        if token.startswith(USER_TOKEN_PREFIX):
            try:
                with db.connection() as conn:
                    row = db.one(conn, "select dataset from users where api_token_hash = %s and not disabled",
                                 (hash_token(token),))
                return row["dataset"] if row else None
            except Exception:
                log.debug("token lookup failed", exc_info=True)
        return None
    if not write_only_token and "dataset" in session:
        return session["dataset"]
    return None


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if "dataset" not in session:
            return redirect(url_for("web.login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


def check_csrf():
    sent = request.form.get("csrf_token", "")
    if not sent or not hmac.compare_digest(sent, session.get("csrf", "")):
        abort(400, "Invalid form token. Reload the page and try again.")


def device_token_ok():
    expected = current_app.config["DEVICE_TOKEN"]
    sent = request.headers.get("X-Device-Token", "")
    return bool(expected) and hmac.compare_digest(sent.encode(), expected.encode())


# Login rate limit: 5 failures per client IP per 5 minutes (in memory; fine for one gunicorn worker).
_FAILS = {}
MAX_FAILS, WINDOW_S = 5, 300


def _recent(ip):
    now = time.time()
    _FAILS[ip] = [t for t in _FAILS.get(ip, []) if now - t < WINDOW_S]
    return _FAILS[ip]


def locked_out(ip):
    return len(_recent(ip)) >= MAX_FAILS


def record_failure(ip):
    _recent(ip).append(time.time())


def clear_failures(ip):
    _FAILS.pop(ip, None)
