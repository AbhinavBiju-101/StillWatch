import os

try:                       # optional: load a local .env file during development
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _env(name, default=""):
    return os.environ.get(name, default)


class Config:
    SECRET_KEY = _env("SECRET_KEY", "dev-only-change-me")
    DATABASE_URL = _env("DATABASE_URL")            # Supabase Postgres connection string
    DEVICE_TOKEN = _env("DEVICE_TOKEN")            # shared secret the ESP32 sends
    DEMO_PASSWORD = _env("DEMO_PASSWORD")          # empty = account disabled
    TESTING_PASSWORD = _env("TESTING_PASSWORD")    # empty = account disabled

    # LLM interpreter: none | gemini | ollama
    LLM_PROVIDER = _env("LLM_PROVIDER", "none")
    GEMINI_API_KEY = _env("GEMINI_API_KEY")
    GEMINI_MODEL = _env("GEMINI_MODEL", "gemini-2.5-flash")   # check current model names
    OLLAMA_URL = _env("OLLAMA_URL", "http://localhost:11434")
    OLLAMA_MODEL = _env("OLLAMA_MODEL", "llama3.2:3b")

    # One-click sign-in buttons. Demo only shows simulated data, so it is on by default.
    # Testing opens the REAL pilot data with no password: enable only on your own machine.
    QUICK_LOGIN_DEMO = _env("QUICK_LOGIN_DEMO", "1") == "1"
    QUICK_LOGIN_TESTING = _env("QUICK_LOGIN_TESTING", "0") == "1"

    # SMS / WhatsApp alerts for pilot tanks: none | twilio. Never used for the demo dataset.
    NOTIFY_PROVIDER = _env("NOTIFY_PROVIDER", "none")
    TWILIO_ACCOUNT_SID = _env("TWILIO_ACCOUNT_SID")
    TWILIO_AUTH_TOKEN = _env("TWILIO_AUTH_TOKEN")
    TWILIO_SMS_FROM = _env("TWILIO_SMS_FROM")                 # e.g. +15005550006
    TWILIO_WHATSAPP_FROM = _env("TWILIO_WHATSAPP_FROM")       # e.g. whatsapp:+14155238886
    PUBLIC_BASE_URL = _env("PUBLIC_BASE_URL")                 # e.g. https://stillwatch.onrender.com, for links in messages

    # Per-device tokens (flask --app app register-device) are the normal way in. The shared DEVICE_TOKEN
    # above keeps working for devices that have no token of their own; set to 0 to turn that off.
    ALLOW_SHARED_DEVICE_TOKEN = _env("ALLOW_SHARED_DEVICE_TOKEN", "1") == "1"
    SWEEP_TOKEN = _env("SWEEP_TOKEN")                 # lets an uptime pinger call POST /api/sweep; empty = off
    DEVICE_SILENT_MIN = int(_env("DEVICE_SILENT_MIN", "30"))      # no contact for this long -> "device offline" alert
    DEVICE_READ_PERIOD_S = int(_env("DEVICE_READ_PERIOD_S", "10"))    # sent to devices at check-in
    DEVICE_SEND_PERIOD_S = int(_env("DEVICE_SEND_PERIOD_S", "60"))
    MAX_CONTENT_LENGTH = int(_env("MAX_UPLOAD_MB", "6")) * 1024 * 1024   # lab report uploads are the largest request

    ALERT_COOLDOWN_MIN = int(_env("ALERT_COOLDOWN_MIN", "180"))
    DEMO_REFRESH_HOURS = float(_env("DEMO_REFRESH_HOURS", "2"))
    BEHIND_PROXY = _env("BEHIND_PROXY", "0") == "1"   # 1 on Render: trust X-Forwarded-For/-Proto
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _env("COOKIE_SECURE", "0") == "1"   # set to 1 on Render (HTTPS)
