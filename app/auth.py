"""Two accounts, no user table:
  demo    -> sees only the simulated 'demo' dataset
  testing -> sees only the real 'pilot' dataset (live device data)
Passwords come from environment variables; an empty password disables the account.
"""
import hmac
import secrets
import time
from functools import wraps

from flask import abort, current_app, redirect, request, session, url_for

ACCOUNTS = {"demo": ("DEMO_PASSWORD", "demo"), "testing": ("TESTING_PASSWORD", "pilot")}


def check_login(username, password):
    """Returns the dataset name ('demo' / 'pilot') if credentials are valid, else None."""
    entry = ACCOUNTS.get(username)
    if not entry:
        return None
    expected = current_app.config[entry[0]]
    if expected and hmac.compare_digest(password.encode(), expected.encode()):
        return entry[1]
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
